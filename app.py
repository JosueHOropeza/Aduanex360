"""
============================================================================
 ADUANEX 360 — Sistema de Alta de Clientes
 Streamlit + Supabase

 RUTEO:
   Sin parámetros              -> Módulo del Vendedor (requiere contraseña)
   ?token=<uuid>               -> Módulo del Cliente (acceso por liga)

 Correr local:  streamlit run app.py
============================================================================
"""

import re

import streamlit as st

from lib.db import (
    buscar_clientes,
    crear_cliente,
    crear_dato_fiscal,
    finalizar_alta,
    listar_clientes,
    listar_datos_fiscales,
    reemplazar_csf,
    url_firmada,
    validar_token,
)
from lib.sat import USO_CFDI, regimenes_para

st.set_page_config(
    page_title="Aduanex 360 · Alta de Clientes",
    page_icon="📦",
    layout="centered",
)

# Colores de marca Aduanex
st.markdown("""
<style>
  .stApp header { background: transparent; }
  h1, h2, h3 { color: #0D1B3E; }
  .stButton > button { background: #E85C0D; color: white; border: none;
                       font-weight: 600; }
  .stButton > button:hover { background: #FF7A2F; color: white; }
  .clave-box { background: #0D1B3E; color: #FF7A2F; padding: 1rem;
               border-radius: 8px; font-size: 1.6rem; font-weight: 700;
               text-align: center; letter-spacing: 2px; }
</style>
""", unsafe_allow_html=True)


# ===========================================================================
#  UTILIDADES
# ===========================================================================

def obtener_ip() -> str:
    """
    IP del cliente — evidencia de consentimiento para la LFPDPPP.

    Streamlit no expone la IP de forma oficial y estable. Esto usa una API
    interna que PUEDE romperse al actualizar Streamlit; por eso va en
    try/except y nunca tumba la app.

    En producción detrás de Nginx/Cloudflare, lo correcto es leer el header
    X-Forwarded-For. Para el prototipo, esto basta.
    """
    try:
        from streamlit.web.server.websocket_headers import _get_websocket_headers
        h = _get_websocket_headers() or {}
        fwd = h.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() if fwd else "desconocida"
    except Exception:
        return "desconocida"


def validar_formulario(d: dict) -> list:
    """
    Validación en el navegador. RÁPIDA, para dar feedback inmediato.

    OJO: esto NO es seguridad. Es cortesía. La validación DE VERDAD está
    en la función de Postgres, que nadie puede saltarse. Aquí solo evitamos
    un viaje al servidor para errores obvios.
    """
    errores = []

    if len(d["nombre"].strip()) < 2:
        errores.append("El nombre comercial es obligatorio.")

    tel = re.sub(r"[\s\-()]", "", d["telefono"])
    if not re.match(r"^\+?[0-9]{10,15}$", tel):
        errores.append("Teléfono inválido (10 a 15 dígitos).")

    if d["correo"] and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", d["correo"]):
        errores.append("Correo inválido.")

    if not re.match(r"^[0-9]{5}$", d["cp"].strip()):
        errores.append("Código postal inválido (5 dígitos).")

    for campo, etiqueta in [
        ("calle", "Calle"), ("num_ext", "Número exterior"),
        ("colonia", "Colonia"), ("municipio", "Municipio"), ("estado", "Estado"),
    ]:
        if not d[campo].strip():
            errores.append(f"{etiqueta} es obligatorio.")

    return errores


# ===========================================================================
#  MÓDULO 1 — VENDEDOR
# ===========================================================================

def modulo_vendedor():
    st.title("📦 Aduanex 360")
    st.caption("Alta de clientes · Panel interno")

    # --- Puerta de acceso ---
    # Contraseña compartida. Suficiente para 2-5 personas en un prototipo.
    # NO es suficiente para producción: no sabes QUIÉN capturó cada cliente.
    # Cuando el sistema sea crítico, cámbialo por Supabase Auth con un
    # usuario por vendedor (viene explicado en el README).
    if not st.session_state.get("autenticado"):
        st.subheader("Acceso")
        pwd = st.text_input("Contraseña", type="password")
        if st.button("Entrar"):
            if pwd and pwd == st.secrets.get("APP_PASSWORD"):
                st.session_state.autenticado = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
        st.stop()

    tab_alta, tab_fiscal, tab_lista = st.tabs(
        ["➕ Nuevo cliente", "🧾 Datos fiscales", "📋 Clientes"]
    )

    # -------------------------------------------------------------------
    with tab_alta:
        # Si acabamos de crear un cliente, mostramos la liga y salimos.
        if "recien_creado" in st.session_state:
            c = st.session_state.recien_creado

            st.success("Cliente creado correctamente.")
            st.markdown(
                f'<div class="clave-box">{c["clave"]}</div>',
                unsafe_allow_html=True,
            )

            base = st.secrets.get("APP_URL", "http://localhost:8501")
            liga = f'{base}?token={c["token_seguridad"]}'

            st.markdown("#### Liga de seguridad para el cliente")
            st.code(liga, language=None)
            st.caption(
                "⏳ Caduca en 7 días · 🔒 Un solo uso · "
                "Envíala por WhatsApp al cliente."
            )

            # Botón de WhatsApp con el mensaje precargado
            mensaje = (
                f"¡Hola! Soy tu asesor de Aduanex 360. "
                f"Tu clave de cliente es {c['clave']}.\n\n"
                f"Para completar tu alta, entra a esta liga segura "
                f"y sube tu identificación:\n{liga}\n\n"
                f"La liga es personal y caduca en 7 días."
            )
            from urllib.parse import quote
            st.link_button(
                "💬 Enviar por WhatsApp",
                f"https://wa.me/?text={quote(mensaje)}",
            )

            if st.button("Capturar otro cliente"):
                del st.session_state.recien_creado
                st.rerun()
            st.stop()

        # --- Formulario de captura ---
        with st.form("alta_cliente", clear_on_submit=False):
            st.subheader("Datos del cliente")
            nombre   = st.text_input("Nombre comercial *")
            col1, col2 = st.columns(2)
            telefono = col1.text_input("Teléfono *", placeholder="8135576111")
            correo   = col2.text_input("Correo", placeholder="cliente@empresa.com")

            st.subheader("Dirección inicial")
            calle = st.text_input("Calle *")
            c1, c2 = st.columns(2)
            num_ext = c1.text_input("Núm. exterior *")
            num_int = c2.text_input("Núm. interior")

            c3, c4 = st.columns(2)
            colonia   = c3.text_input("Colonia *")
            cp        = c4.text_input("Código postal *", max_chars=5)

            c5, c6 = st.columns(2)
            municipio = c5.text_input("Municipio *")
            estado    = c6.text_input("Estado *")

            pais = st.selectbox("País", ["MX", "US", "CN"], index=0)
            referencias = st.text_area("Referencias", height=68)

            enviado = st.form_submit_button("Guardar y generar liga")

        if enviado:
            datos = {
                "nombre": nombre, "telefono": telefono, "correo": correo,
                "calle": calle, "num_ext": num_ext, "num_int": num_int,
                "colonia": colonia, "municipio": municipio, "estado": estado,
                "cp": cp, "pais": pais, "referencias": referencias,
            }

            errores = validar_formulario(datos)
            if errores:
                for e in errores:
                    st.error(e)
            else:
                try:
                    with st.spinner("Guardando..."):
                        st.session_state.recien_creado = crear_cliente(datos)
                    st.rerun()
                except Exception as e:
                    st.error(f"No se pudo guardar: {e}")

    # -------------------------------------------------------------------
    with tab_fiscal:
        modulo_fiscal()

    # -------------------------------------------------------------------
    with tab_lista:
        st.subheader("Últimos clientes")
        try:
            clientes = listar_clientes()
            if not clientes:
                st.info("Aún no hay clientes capturados.")
            else:
                for c in clientes:
                    estado = "✅ Completado" if c["link_completado"] else "⏳ Pendiente"
                    st.markdown(
                        f"**{c['clave']}** · {c['nombre']} · "
                        f"{c['telefono']} · {estado}"
                    )
        except Exception as e:
            st.error(f"No se pudo cargar la lista: {e}")


# ===========================================================================
#  MÓDULO 1-B — DATOS FISCALES (lo captura el ASESOR, no el cliente)
# ===========================================================================
#
#  ¿Por qué lo captura el asesor y no el cliente?
#
#  Porque el régimen fiscal y el uso de CFDI son claves del SAT que el
#  cliente casi nunca conoce de memoria. Si le pides que las elija, va a
#  adivinar — y un régimen equivocado hace que el CFDI se RECHACE al
#  timbrarlo. El asesor las lee directo de la Constancia de Situación
#  Fiscal, que es el documento oficial.
#
#  Por eso la CSF es OBLIGATORIA para dar de alta una razón social:
#  es la fuente de verdad, no el formulario.
# ===========================================================================

def modulo_fiscal():
    st.subheader("Datos fiscales")
    st.caption(
        "Captura las razones sociales del cliente. "
        "Un cliente puede tener varias (empresa, persona física, etc.)."
    )

    # --- 1. Seleccionar el cliente ---
    busqueda = st.text_input(
        "Buscar cliente",
        placeholder="ANDO-100, nombre o teléfono...",
        key="busca_fiscal",
    )

    try:
        clientes = buscar_clientes(busqueda)
    except Exception as e:
        st.error(f"No se pudo buscar: {e}")
        return

    if not clientes:
        st.info("No se encontraron clientes. Captúralo primero en 'Nuevo cliente'.")
        return

    opciones = {f"{c['clave']} — {c['nombre']}": c for c in clientes}
    elegido = st.selectbox("Cliente", list(opciones.keys()))
    cliente = opciones[elegido]

    st.divider()

    # --- 2. Razones sociales ya registradas ---
    try:
        fiscales = listar_datos_fiscales(cliente["id"])
    except Exception as e:
        st.error(f"No se pudieron cargar los datos fiscales: {e}")
        return

    if fiscales:
        st.markdown("#### Razones sociales registradas")
        for f in fiscales:
            etiqueta = "⭐ Principal" if f["es_principal"] else ""
            with st.expander(f"**{f['rfc']}** · {f['razon_social']}  {etiqueta}"):
                c1, c2 = st.columns(2)
                c1.markdown(f"**Régimen:** {f['regimen_fiscal']}")
                c1.markdown(f"**Uso CFDI:** {f['uso_cfdi']}")
                c2.markdown(f"**CP fiscal:** {f['cp_fiscal']}")
                c2.markdown(f"**CSF versión:** {f.get('csf_version', '—')}")

                # La CSF vive en un bucket PRIVADO. Esta URL caduca en 5 min.
                if f.get("csf_path"):
                    try:
                        st.link_button(
                            "📄 Ver constancia",
                            url_firmada(f["csf_path"]),
                        )
                        st.caption("La liga caduca en 5 minutos.")
                    except Exception:
                        st.caption("No se pudo generar la liga del documento.")

                # Reemplazar la CSF (versionado, no sobrescritura)
                st.markdown("**Actualizar constancia**")
                st.caption(
                    "La versión anterior NO se borra: queda en el historial."
                )
                nueva = st.file_uploader(
                    "Nueva CSF",
                    type=["pdf", "jpg", "jpeg", "png"],
                    key=f"csf_upd_{f['dato_fiscal_id']}",
                    label_visibility="collapsed",
                )
                if nueva and st.button(
                    "Reemplazar CSF", key=f"btn_upd_{f['dato_fiscal_id']}"
                ):
                    try:
                        with st.spinner("Subiendo..."):
                            reemplazar_csf(
                                f["dato_fiscal_id"], cliente["clave"], nueva
                            )
                        st.success("Constancia actualizada.")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"No se pudo actualizar: {e}")
    else:
        st.info("Este cliente aún no tiene razones sociales registradas.")

    st.divider()

    # --- 3. Agregar razón social nueva ---
    st.markdown("#### Agregar razón social")

    # OJO: el RFC va FUERA del st.form.
    #
    # Motivo: dentro de un form, Streamlit no vuelve a ejecutar el script
    # hasta que le das submit. Necesitamos el RFC ANTES, para saber si es
    # persona física (13 caracteres) o moral (12) y mostrarle al asesor
    # SOLO los regímenes que aplican. Mostrarle 'Sueldos y Salarios' a una
    # S.A. de C.V. es invitarlo a equivocarse.
    rfc = st.text_input(
        "RFC *",
        max_chars=13,
        placeholder="ABC123456XY1",
        help="12 caracteres = empresa · 13 = persona física. Cópialo tal cual de la CSF.",
        key="rfc_nuevo",
    ).strip().upper()

    if rfc and len(rfc) >= 12:
        tipo = "Persona moral (empresa)" if len(rfc) == 12 else "Persona física"
        st.caption(f"Detectado: **{tipo}**")

    regimenes = regimenes_para(rfc)

    with st.form("nueva_razon_social", clear_on_submit=True):
        razon = st.text_input(
            "Razón social *",
            placeholder="COMERCIALIZADORA EJEMPLO SA DE CV",
            help="Cópiala EXACTAMENTE como aparece en la CSF, sin puntos ni comas de más.",
        )

        c1, c2 = st.columns(2)
        regimen = c1.selectbox(
            "Régimen fiscal *",
            options=list(regimenes.keys()),
            format_func=lambda k: regimenes[k],
            help="Está en la CSF. Si no coincide, el CFDI se rechaza al timbrar.",
        )
        cp_fiscal = c2.text_input(
            "CP fiscal *",
            max_chars=5,
            placeholder="64000",
            help="Obligatorio en CFDI 4.0. Es el CP del domicilio fiscal, no el de envío.",
        )

        c3, c4 = st.columns(2)
        uso = c3.selectbox(
            "Uso de CFDI *",
            options=list(USO_CFDI.keys()),
            index=list(USO_CFDI.keys()).index("G03"),  # el típico en logística
            format_func=lambda k: USO_CFDI[k],
        )
        email_fact = c4.text_input(
            "Correo de facturación",
            placeholder="facturas@empresa.com",
        )

        principal = st.checkbox(
            "Marcar como razón social principal",
            help="La que se usa por defecto al facturar. Solo puede haber una.",
        )

        st.markdown("**Constancia de Situación Fiscal (CSF) ***")
        st.caption("PDF o imagen · Máx. 10 MB · Obligatoria.")
        csf = st.file_uploader(
            "CSF",
            type=["pdf", "jpg", "jpeg", "png"],
            label_visibility="collapsed",
        )

        guardar = st.form_submit_button("Guardar razón social")

    if guardar:
        errores = []

        if not re.match(r"^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$", rfc):
            errores.append("RFC con formato inválido (12 o 13 caracteres).")
        if len(razon.strip()) < 3:
            errores.append("La razón social es obligatoria.")
        if not re.match(r"^[0-9]{5}$", cp_fiscal.strip()):
            errores.append("CP fiscal inválido (5 dígitos).")
        if email_fact and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_fact):
            errores.append("Correo de facturación inválido.")
        if csf is None:
            errores.append(
                "La Constancia de Situación Fiscal es obligatoria. "
                "Sin ella no hay cómo verificar el RFC y el régimen."
            )

        # Duplicado: el índice único de la base lo bloquearía de todas formas,
        # pero es mejor decírselo al asesor antes de subir un archivo de 8 MB.
        if any(f["rfc"] == rfc for f in fiscales):
            errores.append(f"El RFC {rfc} ya está registrado para este cliente.")

        if errores:
            for e in errores:
                st.error(e)
        else:
            try:
                with st.spinner("Guardando razón social y constancia..."):
                    crear_dato_fiscal(
                        cliente_id=cliente["id"],
                        clave=cliente["clave"],
                        datos={
                            "rfc": rfc,
                            "razon_social": razon,
                            "regimen": regimen,
                            "uso_cfdi": uso,
                            "cp_fiscal": cp_fiscal,
                            "email": email_fact,
                            "principal": principal,
                        },
                        csf_archivo=csf,
                    )
                st.success(f"Razón social {rfc} guardada correctamente.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"No se pudo guardar: {e}")


# ===========================================================================
#  MÓDULO 2 — CLIENTE (acceso por liga)
# ===========================================================================

AVISO_PRIVACIDAD = """
**AVISO DE PRIVACIDAD**

**Aduanex 360**, con domicilio en Monterrey, Nuevo León, es responsable del
tratamiento de sus datos personales.

**Datos que recabamos:** nombre, teléfono, correo electrónico, domicilio,
datos fiscales (RFC, razón social) e identificación oficial (INE).

**Finalidades primarias:** integrar su expediente como cliente, prestar los
servicios de logística internacional contratados, emitir comprobantes
fiscales y cumplir obligaciones legales en materia de comercio exterior.

**Transferencias:** sus datos podrán compartirse con agentes aduanales y
autoridades competentes, únicamente cuando sea necesario para la prestación
del servicio o por mandato de ley.

**Derechos ARCO:** usted puede Acceder, Rectificar, Cancelar u Oponerse al
tratamiento de sus datos escribiendo a **contacto@aduanex360.com**.

Al marcar la casilla y firmar, usted otorga su consentimiento expreso para el
tratamiento de sus datos personales conforme a este aviso.
"""


def modulo_cliente(token: str):
    st.title("📦 Aduanex 360")
    st.caption("Completa tu alta como cliente")

    resultado = validar_token(token)

    # --- Liga inválida, usada o expirada ---
    if not resultado.get("valido"):
        st.error(f"🔒 {resultado.get('motivo', 'Acceso denegado.')}")
        st.info(
            "Si necesitas una liga nueva, contacta a tu asesor por WhatsApp: "
            "**+52 181 3557 6111**"
        )
        st.stop()

    # --- Pantalla de éxito (después de finalizar) ---
    if st.session_state.get("alta_completada"):
        st.success("✅ ¡Listo! Tu alta quedó completada.")
        st.balloons()
        st.markdown(
            f"Tu clave de cliente es **{resultado['clave']}**. "
            "Tu asesor se pondrá en contacto contigo."
        )
        st.stop()

    st.markdown(f"Hola, **{resultado['nombre']}** 👋")
    st.markdown(f"Tu clave de cliente: **{resultado['clave']}**")
    st.divider()

    # --- 1. Aviso de privacidad ---
    st.subheader("1. Aviso de privacidad")
    with st.container(height=260, border=True):
        st.markdown(AVISO_PRIVACIDAD)

    acepta = st.checkbox(
        "He leído y **acepto** el Aviso de Privacidad, y autorizo el "
        "tratamiento de mis datos personales."
    )

    st.divider()

    # --- 2. Firma ---
    st.subheader("2. Firma")
    st.caption("Dibuja tu firma con el mouse o con el dedo.")

    from streamlit_drawable_canvas import st_canvas

    canvas = st_canvas(
        stroke_width=3,
        stroke_color="#0D1B3E",
        background_color="#FFFFFF",
        height=180,
        width=400,
        drawing_mode="freedraw",
        key="firma",
    )

    st.divider()

    # --- 3. INE ---
    st.subheader("3. Identificación oficial (INE)")
    st.caption("PDF o imagen · Máximo 10 MB · Ambos lados en un solo archivo.")

    ine = st.file_uploader(
        "Sube tu INE",
        type=["pdf", "jpg", "jpeg", "png"],
        label_visibility="collapsed",
    )

    st.divider()

    # --- Finalizar ---
    if st.button("Finalizar registro", type="primary", use_container_width=True):

        # Validaciones antes de tocar la base
        if not acepta:
            st.error("Debes aceptar el Aviso de Privacidad para continuar.")
            st.stop()

        if canvas.image_data is None or canvas.json_data is None \
                or not canvas.json_data.get("objects"):
            st.error("Por favor dibuja tu firma.")
            st.stop()

        if ine is None:
            st.error("Por favor sube tu identificación oficial.")
            st.stop()

        # Convertir el canvas a PNG
        import io
        from PIL import Image

        img = Image.fromarray(canvas.image_data.astype("uint8"), mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        firma_bytes = buf.getvalue()

        try:
            with st.spinner("Guardando tu información de forma segura..."):
                finalizar_alta(
                    token=token,
                    ip=obtener_ip(),
                    firma_bytes=firma_bytes,
                    ine_archivo=ine,
                    clave=resultado["clave"],
                )
            st.session_state.alta_completada = True
            st.rerun()

        except ValueError as e:
            # Errores esperables del usuario (archivo grande, formato malo)
            st.error(str(e))
        except Exception as e:
            # Errores del sistema. No muestres el traceback al cliente.
            st.error(
                "Ocurrió un error al guardar. Intenta de nuevo o contacta a "
                "tu asesor al +52 181 3557 6111."
            )
            print(f"[ERROR finalizar_alta] {e}")  # queda en el log del servidor


# ===========================================================================
#  RUTEO
# ===========================================================================

token = st.query_params.get("token")

if token:
    modulo_cliente(token)
else:
    modulo_vendedor()
