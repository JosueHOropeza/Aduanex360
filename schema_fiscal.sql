-- =====================================================================
--  ADUANEX 360 — Módulo de Datos Fiscales
--  Ejecutar DESPUÉS de schema.sql, en el SQL Editor de Supabase.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. Columnas de DATOS_FISCALES
-- ---------------------------------------------------------------------
ALTER TABLE datos_fiscales
  ADD COLUMN IF NOT EXISTS rfc               VARCHAR(13) NOT NULL,
  ADD COLUMN IF NOT EXISTS razon_social      VARCHAR(250) NOT NULL,
  ADD COLUMN IF NOT EXISTS regimen_fiscal    VARCHAR(3)  NOT NULL,  -- clave SAT: 601, 626...
  ADD COLUMN IF NOT EXISTS uso_cfdi          VARCHAR(4)  NOT NULL DEFAULT 'G03',
  ADD COLUMN IF NOT EXISTS cp_fiscal         CHAR(5)     NOT NULL,  -- obligatorio en CFDI 4.0
  ADD COLUMN IF NOT EXISTS email_facturacion VARCHAR(150),
  ADD COLUMN IF NOT EXISTS es_principal      BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS activo            BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS creado_en         TIMESTAMPTZ NOT NULL DEFAULT now();


-- ---------------------------------------------------------------------
-- 2. Validaciones a nivel base de datos
-- ---------------------------------------------------------------------
-- RFC: 12 caracteres (persona moral) o 13 (persona física).
-- Este regex valida FORMATO, no existencia. El RFC real solo lo confirma
-- el SAT — por eso pedimos la CSF: es la prueba.
ALTER TABLE datos_fiscales DROP CONSTRAINT IF EXISTS chk_rfc;
ALTER TABLE datos_fiscales ADD CONSTRAINT chk_rfc
  CHECK (rfc ~ '^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$');

ALTER TABLE datos_fiscales DROP CONSTRAINT IF EXISTS chk_cp_fiscal;
ALTER TABLE datos_fiscales ADD CONSTRAINT chk_cp_fiscal
  CHECK (cp_fiscal ~ '^[0-9]{5}$');

-- Un cliente no puede tener el mismo RFC dos veces.
CREATE UNIQUE INDEX IF NOT EXISTS uq_fiscal_cliente_rfc
  ON datos_fiscales (cliente_id, rfc) WHERE activo;

-- Solo UNA razón social principal por cliente (la de facturación por defecto).
CREATE UNIQUE INDEX IF NOT EXISTS uq_fiscal_principal
  ON datos_fiscales (cliente_id) WHERE es_principal AND activo;

CREATE INDEX IF NOT EXISTS idx_fiscal_cliente ON datos_fiscales (cliente_id);


-- ---------------------------------------------------------------------
-- 3. CORRECCIÓN IMPORTANTE del índice de documentos
-- ---------------------------------------------------------------------
-- El índice original (schema.sql) decía: una sola CSF vigente por cliente.
-- ESO ESTÁ MAL. Si el cliente tiene 3 razones sociales, necesita 3 CSF
-- vigentes al mismo tiempo — una por cada RFC.
--
-- Regla correcta:
--   INE / CONTRATO / FIRMA -> uno vigente por CLIENTE
--   CSF                    -> uno vigente por RAZÓN SOCIAL

ALTER TABLE documentos
  ADD COLUMN IF NOT EXISTS dato_fiscal_id BIGINT
      REFERENCES datos_fiscales(id) ON DELETE CASCADE;

DROP INDEX IF EXISTS uq_doc_vigente;

-- Documentos que son del cliente (no de una razón social específica)
CREATE UNIQUE INDEX uq_doc_vigente_cliente
  ON documentos (cliente_id, tipo)
  WHERE vigente AND tipo <> 'CSF';

-- La CSF va amarrada a la razón social, no al cliente
CREATE UNIQUE INDEX uq_doc_vigente_csf
  ON documentos (dato_fiscal_id)
  WHERE vigente AND tipo = 'CSF';

-- Una CSF SIEMPRE debe traer su razón social. Sin ella no sabes de quién es.
ALTER TABLE documentos DROP CONSTRAINT IF EXISTS chk_csf_requiere_fiscal;
ALTER TABLE documentos ADD CONSTRAINT chk_csf_requiere_fiscal
  CHECK (tipo <> 'CSF' OR dato_fiscal_id IS NOT NULL);


-- ---------------------------------------------------------------------
-- 4. RPC — alta de razón social + CSF (transaccional)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION crear_dato_fiscal(
    p_cliente_id   BIGINT,
    p_rfc          TEXT,
    p_razon_social TEXT,
    p_regimen      TEXT,
    p_uso_cfdi     TEXT,
    p_cp_fiscal    TEXT,
    p_email        TEXT,
    p_principal    BOOLEAN,
    -- Datos del archivo de la CSF (ya subido al Storage)
    p_csf_nombre   TEXT,
    p_csf_path     TEXT,
    p_csf_mime     TEXT,
    p_csf_bytes    BIGINT,
    p_csf_hash     TEXT
)
RETURNS TABLE (id BIGINT, rfc VARCHAR)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_id  BIGINT;
    v_rfc TEXT := upper(trim(p_rfc));
BEGIN
    -- Validaciones del lado del servidor. El formulario de Streamlit se
    -- puede saltar; esto no.
    IF v_rfc !~ '^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$' THEN
        RAISE EXCEPTION 'RFC con formato inválido: %', v_rfc;
    END IF;

    IF length(trim(p_razon_social)) < 3 THEN
        RAISE EXCEPTION 'La razón social es obligatoria.';
    END IF;

    IF p_cp_fiscal !~ '^[0-9]{5}$' THEN
        RAISE EXCEPTION 'Código postal fiscal inválido (5 dígitos).';
    END IF;

    -- Si esta será la principal, desmarca la anterior.
    -- Va DENTRO de la transacción: nunca quedan dos principales,
    -- ni cero principales.
    IF p_principal THEN
        UPDATE datos_fiscales
        SET es_principal = FALSE
        WHERE cliente_id = p_cliente_id AND es_principal;
    END IF;

    INSERT INTO datos_fiscales (
        cliente_id, rfc, razon_social, regimen_fiscal,
        uso_cfdi, cp_fiscal, email_facturacion, es_principal
    ) VALUES (
        p_cliente_id, v_rfc, trim(p_razon_social), p_regimen,
        p_uso_cfdi, p_cp_fiscal, nullif(trim(p_email), ''),
        -- Si es la primera razón social del cliente, se vuelve principal
        -- automáticamente. Nunca debe haber un cliente con razones
        -- sociales pero sin ninguna principal.
        p_principal OR NOT EXISTS (
            SELECT 1 FROM datos_fiscales
            WHERE cliente_id = p_cliente_id AND activo
        )
    )
    RETURNING datos_fiscales.id INTO v_id;

    -- La CSF, ligada a ESTA razón social
    INSERT INTO documentos (
        cliente_id, dato_fiscal_id, tipo, nombre_original,
        storage_path, mime_type, tamano_bytes, hash_sha256
    ) VALUES (
        p_cliente_id, v_id, 'CSF', p_csf_nombre,
        p_csf_path, p_csf_mime, p_csf_bytes, p_csf_hash
    );

    RETURN QUERY SELECT v_id, v_rfc::VARCHAR;
END;
$$;


-- ---------------------------------------------------------------------
-- 5. RPC — reemplazar la CSF de una razón social (versionado)
-- ---------------------------------------------------------------------
-- Las constancias se actualizan (cambio de régimen, de domicilio).
-- La anterior NO se borra: pasa a vigente = FALSE y sube de versión.
-- Historial completo, que es lo que te salva en una auditoría.

CREATE OR REPLACE FUNCTION reemplazar_csf(
    p_dato_fiscal_id BIGINT,
    p_csf_nombre     TEXT,
    p_csf_path       TEXT,
    p_csf_mime       TEXT,
    p_csf_bytes      BIGINT,
    p_csf_hash       TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_cliente_id BIGINT;
    v_version    INT;
BEGIN
    SELECT cliente_id INTO v_cliente_id
    FROM datos_fiscales WHERE id = p_dato_fiscal_id AND activo;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Razón social no encontrada.';
    END IF;

    SELECT coalesce(max(version), 0) + 1 INTO v_version
    FROM documentos WHERE dato_fiscal_id = p_dato_fiscal_id AND tipo = 'CSF';

    UPDATE documentos SET vigente = FALSE
    WHERE dato_fiscal_id = p_dato_fiscal_id AND tipo = 'CSF' AND vigente;

    INSERT INTO documentos (
        cliente_id, dato_fiscal_id, tipo, nombre_original,
        storage_path, mime_type, tamano_bytes, hash_sha256, version
    ) VALUES (
        v_cliente_id, p_dato_fiscal_id, 'CSF', p_csf_nombre,
        p_csf_path, p_csf_mime, p_csf_bytes, p_csf_hash, v_version
    );

    RETURN TRUE;
END;
$$;


-- ---------------------------------------------------------------------
-- 6. Vista de consulta — expediente fiscal por cliente
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_expediente_fiscal AS
SELECT
    c.id              AS cliente_id,
    c.clave,
    c.nombre          AS cliente,
    df.id             AS dato_fiscal_id,
    df.rfc,
    df.razon_social,
    df.regimen_fiscal,
    df.uso_cfdi,
    df.cp_fiscal,
    df.es_principal,
    d.id              AS csf_id,
    d.storage_path    AS csf_path,
    d.version         AS csf_version,
    d.subido_en       AS csf_subida_en
FROM clientes c
JOIN datos_fiscales df ON df.cliente_id = c.id AND df.activo
LEFT JOIN documentos d ON d.dato_fiscal_id = df.id
                      AND d.tipo = 'CSF' AND d.vigente
WHERE c.activo
ORDER BY c.id DESC, df.es_principal DESC;
