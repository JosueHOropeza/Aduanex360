"""
lib/db.py — Conexión a Supabase y helpers de base de datos / storage.

Toda la comunicación con Supabase pasa por aquí. Ningún archivo de UI
debe hablar directo con la base: así, si mañana cambias de proveedor,
solo tocas este archivo.
"""

import hashlib
import os
import uuid
from datetime import datetime

import streamlit as st
from supabase import Client, create_client

BUCKET = "documentos-clientes"

# Formatos y tamaño permitidos para el INE.
# Se valida DOS veces: aquí y en el CHECK de la tabla documentos.
MIME_PERMITIDOS = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}
MAX_BYTES = 10 * 1024 * 1024  # 10 MB


@st.cache_resource
def conectar() -> Client:
    """
    Cliente de Supabase, cacheado (se crea una sola vez por sesión del servidor).

    SEGURIDAD — la parte que más se equivoca la gente:

    Usamos SERVICE_ROLE_KEY, que ignora el Row Level Security. Es correcto
    AQUÍ porque Streamlit corre en el SERVIDOR: el navegador del cliente
    nunca ve esta llave, solo recibe el HTML ya renderizado.

    Lo que NUNCA debes hacer:
      - Poner la service key en un frontend de JavaScript (ahí sí se ve)
      - Subir el archivo .env o secrets.toml a GitHub
      - Compartir la llave por WhatsApp o correo

    Si la llave se filtra, alguien tiene acceso TOTAL a tu base de datos.
    """
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")

    if not url or not key:
        st.error(
            "Faltan las credenciales de Supabase. "
            "Crea el archivo `.streamlit/secrets.toml` (ver README)."
        )
        st.stop()

    return create_client(url, key)


# ---------------------------------------------------------------------
# VENDEDOR
# ---------------------------------------------------------------------

def crear_cliente(datos: dict) -> dict:
    """
    Crea cliente + dirección en UNA SOLA TRANSACCIÓN.

    Llama a la función RPC de Postgres, no a dos .insert() seguidos.
    Motivo: el SDK de Python hace cada .insert() como un HTTP request
    independiente. Si el segundo falla, el primero YA quedó guardado
    y te queda un cliente huérfano sin dirección.

    La función de Postgres corre todo dentro de un BEGIN...COMMIT:
    si algo truena, se revierte completo.

    Devuelve: {id, clave, token_seguridad}
    """
    sb = conectar()
    resultado = sb.rpc("crear_cliente_con_direccion", {
        "p_nombre":      datos["nombre"],
        "p_telefono":    datos["telefono"],
        "p_correo":      datos.get("correo", ""),
        "p_calle":       datos["calle"],
        "p_num_ext":     datos["num_ext"],
        "p_num_int":     datos.get("num_int", ""),
        "p_colonia":     datos["colonia"],
        "p_municipio":   datos["municipio"],
        "p_estado":      datos["estado"],
        "p_cp":          datos["cp"],
        "p_pais":        datos.get("pais", "MX"),
        "p_referencias": datos.get("referencias", ""),
    }).execute()

    if not resultado.data:
        raise RuntimeError("No se pudo crear el cliente.")

    return resultado.data[0]


def listar_clientes(limite: int = 50) -> list:
    """Últimos clientes, para el panel del vendedor."""
    sb = conectar()
    r = (
        sb.table("clientes")
        .select("id, clave, nombre, telefono, link_completado, creado_en")
        .order("id", desc=True)
        .limit(limite)
        .execute()
    )
    return r.data or []


# ---------------------------------------------------------------------
# CLIENTE (acceso por token)
# ---------------------------------------------------------------------

def validar_token(token: str) -> dict:
    """
    Valida la liga del cliente.

    Devuelve {valido: bool, motivo: str, id, clave, nombre}

    La validación vive en Postgres (RPC), NO aquí. Motivo: la lógica de
    seguridad debe estar lo más cerca posible del dato. Si mañana haces
    una app móvil, la regla ya está aplicada y no hay que reescribirla.
    """
    # Un token malformado no debe llegar a la base. Se rechaza aquí.
    try:
        uuid.UUID(token)
    except (ValueError, AttributeError, TypeError):
        return {"valido": False, "motivo": "Liga inválida."}

    sb = conectar()
    r = sb.rpc("validar_token", {"p_token": token}).execute()

    if not r.data:
        return {"valido": False, "motivo": "Liga inválida."}

    return r.data[0]


def finalizar_alta(token: str, ip: str, firma_bytes: bytes,
                   ine_archivo, clave: str) -> bool:
    """
    Sube firma + INE al bucket privado y cierra el registro.

    ORDEN IMPORTANTE:
      1. Subir los archivos al Storage
      2. Registrar en la base (RPC transaccional)

    Si el paso 2 falla, quedan archivos huérfanos en el bucket (basura,
    pero inofensiva). Si lo hiciéramos al revés y fallara la subida,
    tendrías un registro que apunta a un archivo que no existe — mucho peor.
    """
    sb = conectar()
    hoy = datetime.now().strftime("%Y-%m-%d")

    # --- Validar el INE ANTES de tocar nada ---
    contenido = ine_archivo.getvalue()
    ext = ine_archivo.name.rsplit(".", 1)[-1].lower()

    if ext not in MIME_PERMITIDOS:
        raise ValueError("Formato no permitido. Sube PDF, JPG o PNG.")
    if len(contenido) > MAX_BYTES:
        raise ValueError("El archivo supera los 10 MB.")
    if len(contenido) == 0:
        raise ValueError("El archivo está vacío.")

    mime = MIME_PERMITIDOS[ext]

    # --- Nombres de archivo con UUID ---
    # NUNCA uses el nombre original del archivo en el storage: trae acentos,
    # espacios, y abre la puerta a path traversal (../../etc/passwd).
    # El nombre original se guarda en la BD, en `nombre_original`.
    sufijo = uuid.uuid4().hex[:8]
    ine_path = f"{clave}/INE/{hoy}_{sufijo}.{ext}"
    firma_path = f"{clave}/FIRMA/{hoy}_{sufijo}.png"

    # --- Subir al bucket PRIVADO ---
    sb.storage.from_(BUCKET).upload(
        ine_path, contenido, {"content-type": mime, "upsert": "false"}
    )
    sb.storage.from_(BUCKET).upload(
        firma_path, firma_bytes, {"content-type": "image/png", "upsert": "false"}
    )

    # --- Registrar en la base, transaccionalmente ---
    sb.rpc("finalizar_alta", {
        "p_token":      token,
        "p_ip":         ip,
        "p_firma_path": firma_path,
        # El hash es la evidencia de que la firma no se alteró después.
        # Sin esto, la firma es solo un PNG que cualquiera pudo reemplazar.
        "p_firma_hash": hashlib.sha256(firma_bytes).hexdigest(),
        "p_ine_nombre": ine_archivo.name[:255],
        "p_ine_path":   ine_path,
        "p_ine_mime":   mime,
        "p_ine_bytes":  len(contenido),
        "p_ine_hash":   hashlib.sha256(contenido).hexdigest(),
    }).execute()

    return True


# ---------------------------------------------------------------------
# STORAGE
# ---------------------------------------------------------------------

def _subir_documento(clave: str, tipo: str, archivo) -> dict:
    """
    Valida y sube un documento al bucket privado.

    Devuelve los metadatos que necesita la RPC para registrarlo en la base.
    NO toca la base de datos: eso lo hace la función que llama a esta.
    """
    sb = conectar()
    contenido = archivo.getvalue()
    ext = archivo.name.rsplit(".", 1)[-1].lower()

    if ext not in MIME_PERMITIDOS:
        raise ValueError("Formato no permitido. Sube PDF, JPG o PNG.")
    if len(contenido) > MAX_BYTES:
        raise ValueError("El archivo supera los 10 MB.")
    if len(contenido) == 0:
        raise ValueError("El archivo está vacío.")

    hoy = datetime.now().strftime("%Y-%m-%d")
    sufijo = uuid.uuid4().hex[:8]
    path = f"{clave}/{tipo}/{hoy}_{sufijo}.{ext}"

    sb.storage.from_(BUCKET).upload(
        path,
        contenido,
        {"content-type": MIME_PERMITIDOS[ext], "upsert": "false"},
    )

    return {
        "nombre": archivo.name[:255],
        "path": path,
        "mime": MIME_PERMITIDOS[ext],
        "bytes": len(contenido),
        "hash": hashlib.sha256(contenido).hexdigest(),
    }


def crear_dato_fiscal(cliente_id: int, clave: str, datos: dict,
                      csf_archivo) -> dict:
    """
    Alta de razón social + su Constancia de Situación Fiscal.

    La CSF es OBLIGATORIA. Sin ella no tienes cómo comprobar que el RFC y
    el régimen que capturó el asesor son los que el cliente tiene ante el
    SAT — y un régimen equivocado hace que el CFDI se rechace al timbrar.
    Por eso la restricción está en la base (chk_csf_requiere_fiscal),
    no solo en el formulario.
    """
    sb = conectar()
    csf = _subir_documento(clave, "CSF", csf_archivo)

    r = sb.rpc("crear_dato_fiscal", {
        "p_cliente_id":   cliente_id,
        "p_rfc":          datos["rfc"].strip().upper(),
        "p_razon_social": datos["razon_social"],
        "p_regimen":      datos["regimen"],
        "p_uso_cfdi":     datos["uso_cfdi"],
        "p_cp_fiscal":    datos["cp_fiscal"],
        "p_email":        datos.get("email", ""),
        "p_principal":    datos.get("principal", False),
        "p_csf_nombre":   csf["nombre"],
        "p_csf_path":     csf["path"],
        "p_csf_mime":     csf["mime"],
        "p_csf_bytes":    csf["bytes"],
        "p_csf_hash":     csf["hash"],
    }).execute()

    if not r.data:
        raise RuntimeError("No se pudo guardar la razón social.")
    return r.data[0]


def reemplazar_csf(dato_fiscal_id: int, clave: str, csf_archivo) -> bool:
    """
    Sube una CSF nueva. La anterior NO se borra: pasa a vigente = FALSE
    y sube de versión. El historial completo es lo que te salva en una
    auditoría o en una aclaración con el cliente.
    """
    sb = conectar()
    csf = _subir_documento(clave, "CSF", csf_archivo)

    sb.rpc("reemplazar_csf", {
        "p_dato_fiscal_id": dato_fiscal_id,
        "p_csf_nombre":     csf["nombre"],
        "p_csf_path":       csf["path"],
        "p_csf_mime":       csf["mime"],
        "p_csf_bytes":      csf["bytes"],
        "p_csf_hash":       csf["hash"],
    }).execute()
    return True


def listar_datos_fiscales(cliente_id: int) -> list:
    """Razones sociales de un cliente, con su CSF vigente."""
    sb = conectar()
    r = (
        sb.table("v_expediente_fiscal")
        .select("*")
        .eq("cliente_id", cliente_id)
        .execute()
    )
    return r.data or []


def buscar_clientes(texto: str = "", limite: int = 30) -> list:
    """Busca por clave, nombre o teléfono. Para el selector del módulo fiscal."""
    sb = conectar()
    q = sb.table("clientes").select("id, clave, nombre, telefono").eq("activo", True)

    if texto and texto.strip():
        t = texto.strip()
        # or_ construye:  clave ILIKE %t% OR nombre ILIKE %t% OR telefono ILIKE %t%
        q = q.or_(f"clave.ilike.%{t}%,nombre.ilike.%{t}%,telefono.ilike.%{t}%")

    return (q.order("id", desc=True).limit(limite).execute()).data or []


def url_firmada(path: str, segundos: int = 300) -> str:
    """
    URL temporal para ver un documento del bucket PRIVADO.

    El bucket NO es público. Esto genera una liga que caduca en 5 minutos.
    Si el bucket fuera público, los INE de tus clientes vivirían en URLs
    permanentes y adivinables. Eso es una fuga de datos personales.
    """
    sb = conectar()
    r = sb.storage.from_(BUCKET).create_signed_url(path, segundos)
    return r.get("signedURL", "")
