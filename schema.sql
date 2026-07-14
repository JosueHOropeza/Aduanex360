-- =====================================================================
--  ADUANEX 360 — Esquema para Supabase (PostgreSQL)
--  Ejecutar en: Supabase Dashboard > SQL Editor > New Query
--
--  IMPORTANTE: este script ASUME que ya tienes clientes/direcciones/
--  datos_fiscales. Usa ADD COLUMN IF NOT EXISTS para no romper nada.
--  Si tus tablas ya existen con otros nombres de columna, ajusta.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. CLAVE ANDO-### — secuencia atómica (resuelve la concurrencia)
-- ---------------------------------------------------------------------
-- Una SEQUENCE de Postgres es atómica: si A y B piden número al mismo
-- tiempo, A recibe 101 y B recibe 102. Nunca el mismo.
--
-- Si A abandona la captura, el 101 queda como HUECO. NO se recicla.
-- Motivo: la clave pudo haberse comunicado al cliente (correo, contrato
-- impreso). Reciclarla haría que ANDO-101 sea dos personas distintas.
-- Un hueco no le hace daño a nadie. Un duplicado sí.

CREATE SEQUENCE IF NOT EXISTS seq_clave_cliente START 100 INCREMENT 1;


-- ---------------------------------------------------------------------
-- 2. Columnas nuevas en CLIENTES
-- ---------------------------------------------------------------------
ALTER TABLE clientes
  ADD COLUMN IF NOT EXISTS clave            VARCHAR(20)
      DEFAULT ('ANDO-' || nextval('seq_clave_cliente')),
  ADD COLUMN IF NOT EXISTS token_seguridad  UUID DEFAULT gen_random_uuid(),
  ADD COLUMN IF NOT EXISTS token_expira_en  TIMESTAMPTZ
      DEFAULT (now() + interval '7 days'),   -- la liga caduca
  ADD COLUMN IF NOT EXISTS link_completado  BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS aviso_aceptado   BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS aviso_aceptado_en TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS aviso_ip         VARCHAR(45),  -- evidencia LFPDPPP
  ADD COLUMN IF NOT EXISTS firma_path       TEXT,
  ADD COLUMN IF NOT EXISTS firma_hash       CHAR(64),     -- SHA-256 de la firma
  ADD COLUMN IF NOT EXISTS activo           BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS creado_en        TIMESTAMPTZ NOT NULL DEFAULT now();

-- Unicidad e índices
CREATE UNIQUE INDEX IF NOT EXISTS uq_clientes_clave  ON clientes (clave);
CREATE UNIQUE INDEX IF NOT EXISTS uq_clientes_token  ON clientes (token_seguridad);


-- ---------------------------------------------------------------------
-- 3. La clave es INMUTABLE — trigger que bloquea cualquier UPDATE
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_clave_inmutable() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.clave IS DISTINCT FROM OLD.clave THEN
    RAISE EXCEPTION 'La clave de cliente es inmutable (% -> %)', OLD.clave, NEW.clave;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_clave_inmutable ON clientes;
CREATE TRIGGER trg_clave_inmutable
  BEFORE UPDATE ON clientes
  FOR EACH ROW EXECUTE FUNCTION fn_clave_inmutable();


-- ---------------------------------------------------------------------
-- 4. Tabla de DOCUMENTOS (INE, CSF, contrato) — versionada
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documentos (
    id              BIGSERIAL PRIMARY KEY,
    cliente_id      BIGINT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    tipo            VARCHAR(20) NOT NULL
                    CHECK (tipo IN ('INE','CSF','CONTRATO','FIRMA','OTRO')),
    nombre_original VARCHAR(255) NOT NULL,
    storage_path    TEXT NOT NULL UNIQUE,   -- ruta dentro del bucket privado
    mime_type       VARCHAR(100) NOT NULL,
    tamano_bytes    BIGINT NOT NULL CHECK (tamano_bytes > 0 AND tamano_bytes <= 10485760),
    hash_sha256     CHAR(64) NOT NULL,      -- detecta duplicados y corrupción
    version         INT NOT NULL DEFAULT 1,
    vigente         BOOLEAN NOT NULL DEFAULT TRUE,
    subido_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Solo UN documento vigente por cliente y tipo.
-- Las versiones anteriores NO se borran: pasan a vigente = FALSE.
CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_vigente
  ON documentos (cliente_id, tipo) WHERE vigente;

CREATE INDEX IF NOT EXISTS idx_doc_cliente ON documentos (cliente_id);


-- ---------------------------------------------------------------------
-- 5. RPC TRANSACCIONAL — cliente + dirección en un solo COMMIT
-- ---------------------------------------------------------------------
-- ESTE es el punto clave. El SDK de Python NO puede hacer transacciones:
-- cada .insert() es un HTTP request independiente. Si el segundo falla,
-- el primero YA se guardó y te queda un cliente sin dirección.
--
-- Una función de Postgres corre TODO dentro de una transacción implícita.
-- Si algo truena, se revierte completo. Esto es lo correcto.

CREATE OR REPLACE FUNCTION crear_cliente_con_direccion(
    p_nombre       TEXT,
    p_telefono     TEXT,
    p_correo       TEXT,
    p_calle        TEXT,
    p_num_ext      TEXT,
    p_num_int      TEXT,
    p_colonia      TEXT,
    p_municipio    TEXT,
    p_estado       TEXT,
    p_cp           TEXT,
    p_pais         TEXT,
    p_referencias  TEXT
)
RETURNS TABLE (id BIGINT, clave VARCHAR, token_seguridad UUID)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_id    BIGINT;
    v_clave VARCHAR(20);
    v_token UUID;
BEGIN
    -- Validaciones del lado del servidor. No confíes en el formulario:
    -- un usuario puede saltarse la validación de Streamlit, pero no esta.
    IF length(trim(p_nombre)) < 2 THEN
        RAISE EXCEPTION 'El nombre es obligatorio.';
    END IF;

    IF p_telefono !~ '^\+?[0-9]{10,15}$' THEN
        RAISE EXCEPTION 'Teléfono inválido. Usa 10 a 15 dígitos.';
    END IF;

    IF p_correo IS NOT NULL AND p_correo <> '' AND p_correo !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' THEN
        RAISE EXCEPTION 'Correo inválido.';
    END IF;

    IF p_cp !~ '^[0-9]{5}$' THEN
        RAISE EXCEPTION 'Código postal inválido (5 dígitos).';
    END IF;

    -- INSERT 1: el cliente. La clave y el token se generan solos (DEFAULT).
    INSERT INTO clientes (nombre, telefono, correo)
    VALUES (trim(p_nombre), trim(p_telefono), nullif(trim(p_correo), ''))
    RETURNING clientes.id, clientes.clave, clientes.token_seguridad
    INTO v_id, v_clave, v_token;

    -- INSERT 2: la dirección. Si esto falla, el INSERT 1 se revierte.
    INSERT INTO direcciones (
        cliente_id, calle, num_exterior, num_interior, colonia,
        municipio, estado, cp, pais, referencias, es_principal
    ) VALUES (
        v_id, trim(p_calle), trim(p_num_ext), nullif(trim(p_num_int), ''),
        trim(p_colonia), trim(p_municipio), trim(p_estado), trim(p_cp),
        coalesce(nullif(trim(p_pais), ''), 'MX'), nullif(trim(p_referencias), ''),
        TRUE
    );

    RETURN QUERY SELECT v_id, v_clave, v_token;
END;
$$;


-- ---------------------------------------------------------------------
-- 6. RPC — validar token (sin exponer la tabla completa)
-- ---------------------------------------------------------------------
-- Devuelve SOLO lo mínimo necesario para la pantalla del cliente.
-- Nunca expongas la fila entera: trae teléfono, correo y datos de otros.

CREATE OR REPLACE FUNCTION validar_token(p_token UUID)
RETURNS TABLE (
    id BIGINT,
    clave VARCHAR,
    nombre TEXT,
    valido BOOLEAN,
    motivo TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    r RECORD;
BEGIN
    SELECT c.id, c.clave, c.nombre, c.link_completado, c.token_expira_en, c.activo
    INTO r
    FROM clientes c
    WHERE c.token_seguridad = p_token;

    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::BIGINT, NULL::VARCHAR, NULL::TEXT,
                            FALSE, 'Liga inválida.'::TEXT;
        RETURN;
    END IF;

    IF NOT r.activo THEN
        RETURN QUERY SELECT r.id, r.clave, r.nombre::TEXT,
                            FALSE, 'Este registro no está activo.'::TEXT;
        RETURN;
    END IF;

    -- Un solo uso: una vez completado, la liga muere.
    IF r.link_completado THEN
        RETURN QUERY SELECT r.id, r.clave, r.nombre::TEXT,
                            FALSE, 'Este registro ya fue completado.'::TEXT;
        RETURN;
    END IF;

    -- Caducidad: una liga en WhatsApp vive para siempre. Ponle fecha.
    IF r.token_expira_en < now() THEN
        RETURN QUERY SELECT r.id, r.clave, r.nombre::TEXT,
                            FALSE, 'Esta liga expiró. Solicita una nueva a tu asesor.'::TEXT;
        RETURN;
    END IF;

    RETURN QUERY SELECT r.id, r.clave, r.nombre::TEXT, TRUE, 'OK'::TEXT;
END;
$$;


-- ---------------------------------------------------------------------
-- 7. RPC — finalizar alta (transaccional)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION finalizar_alta(
    p_token       UUID,
    p_ip          TEXT,
    p_firma_path  TEXT,
    p_firma_hash  TEXT,
    p_ine_nombre  TEXT,
    p_ine_path    TEXT,
    p_ine_mime    TEXT,
    p_ine_bytes   BIGINT,
    p_ine_hash    TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_id BIGINT;
BEGIN
    -- FOR UPDATE bloquea la fila: si el cliente da doble clic en
    -- "Finalizar", la segunda llamada espera y encuentra link_completado
    -- ya en TRUE. Sin esto, se sube el INE dos veces.
    SELECT c.id INTO v_id
    FROM clientes c
    WHERE c.token_seguridad = p_token
      AND c.link_completado = FALSE
      AND c.token_expira_en > now()
      AND c.activo = TRUE
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Liga inválida, expirada o ya utilizada.';
    END IF;

    -- Versiona el INE anterior si lo hubiera
    UPDATE documentos SET vigente = FALSE
    WHERE cliente_id = v_id AND tipo = 'INE' AND vigente;

    INSERT INTO documentos (cliente_id, tipo, nombre_original, storage_path,
                            mime_type, tamano_bytes, hash_sha256)
    VALUES (v_id, 'INE', p_ine_nombre, p_ine_path,
            p_ine_mime, p_ine_bytes, p_ine_hash);

    UPDATE clientes SET
        link_completado   = TRUE,
        aviso_aceptado    = TRUE,
        aviso_aceptado_en = now(),
        aviso_ip          = p_ip,
        firma_path        = p_firma_path,
        firma_hash        = p_firma_hash
    WHERE id = v_id;

    RETURN TRUE;
END;
$$;


-- ---------------------------------------------------------------------
-- 8. ROW LEVEL SECURITY — cierra las tablas
-- ---------------------------------------------------------------------
-- Sin esto, cualquiera con la anon key (que es PÚBLICA) puede hacer
-- SELECT * FROM clientes y bajarse tu cartera completa. Esto no es opcional.
--
-- Al activar RLS sin políticas, NADIE accede por REST. El acceso pasa
-- únicamente por las funciones SECURITY DEFINER de arriba, que solo
-- devuelven lo que deben.

ALTER TABLE clientes       ENABLE ROW LEVEL SECURITY;
ALTER TABLE direcciones    ENABLE ROW LEVEL SECURITY;
ALTER TABLE datos_fiscales ENABLE ROW LEVEL SECURITY;
ALTER TABLE documentos     ENABLE ROW LEVEL SECURITY;

-- Deja que el rol anónimo ejecute SOLO estas tres funciones:
GRANT EXECUTE ON FUNCTION validar_token(UUID) TO anon;
GRANT EXECUTE ON FUNCTION finalizar_alta(UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BIGINT, TEXT) TO anon;
-- crear_cliente_con_direccion NO se otorga a anon: solo el vendedor
-- autenticado (service_role desde el servidor) puede crear clientes.


-- ---------------------------------------------------------------------
-- 9. BUCKET DE STORAGE — debe ser PRIVADO
-- ---------------------------------------------------------------------
-- Crear desde el Dashboard: Storage > New bucket
--   Nombre:  documentos-clientes
--   Public:  NO  ← si lo dejas público, los INE quedan en URLs adivinables
--
-- Para mostrarlos, se generan URLs firmadas con caducidad de minutos.
-- El código lo hace en lib/db.py > url_firmada()