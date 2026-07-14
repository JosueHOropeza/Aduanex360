-- =====================================================================
--  ADUANEX 360 — PASO 0: Tablas base
--
--  ⚠️  EJECUTA ESTE PRIMERO, ANTES de schema.sql
--
--  ORDEN CORRECTO:
--    1. schema_base.sql    ← este
--    2. schema.sql
--    3. schema_fiscal.sql
--
--  Crea las tres tablas mínimas. Las columnas adicionales (clave ANDO,
--  token, RFC, etc.) las agregan los scripts siguientes.
-- =====================================================================


-- ---------------------------------------------------------------------
-- CLIENTES — entidad principal
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clientes (
    id        BIGSERIAL PRIMARY KEY,
    nombre    VARCHAR(200) NOT NULL,
    telefono  VARCHAR(20)  NOT NULL,
    correo    VARCHAR(150)
);


-- ---------------------------------------------------------------------
-- DIRECCIONES — uno a muchos por cliente
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS direcciones (
    id            BIGSERIAL PRIMARY KEY,
    -- ON DELETE CASCADE: si se borra el cliente, se van sus direcciones.
    -- En la práctica NUNCA borramos clientes (usamos activo = FALSE),
    -- pero la regla protege contra direcciones huérfanas.
    cliente_id    BIGINT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    etiqueta      VARCHAR(50),
    tipo          VARCHAR(20) DEFAULT 'envio'
                  CHECK (tipo IN ('envio','fiscal','ambas')),
    calle         VARCHAR(150) NOT NULL,
    num_exterior  VARCHAR(20)  NOT NULL,
    num_interior  VARCHAR(20),
    colonia       VARCHAR(100) NOT NULL,
    municipio     VARCHAR(100) NOT NULL,
    estado        VARCHAR(50)  NOT NULL,
    cp            CHAR(5)      NOT NULL CHECK (cp ~ '^[0-9]{5}$'),
    pais          CHAR(2)      NOT NULL DEFAULT 'MX',
    referencias   TEXT,
    es_principal  BOOLEAN NOT NULL DEFAULT FALSE,
    activo        BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dir_cliente ON direcciones (cliente_id);

-- Solo UNA dirección principal por cliente
CREATE UNIQUE INDEX IF NOT EXISTS uq_dir_principal
  ON direcciones (cliente_id) WHERE es_principal AND activo;


-- ---------------------------------------------------------------------
-- DATOS_FISCALES — uno a muchos (varias razones sociales por cliente)
-- ---------------------------------------------------------------------
-- Se crea casi vacía a propósito: schema_fiscal.sql le agrega RFC,
-- razón social, régimen, etc. Así los tres scripts encajan sin conflicto.

CREATE TABLE IF NOT EXISTS datos_fiscales (
    id          BIGSERIAL PRIMARY KEY,
    cliente_id  BIGINT NOT NULL REFERENCES clientes(id) ON DELETE CASCADE
);


-- ---------------------------------------------------------------------
-- Verificación
-- ---------------------------------------------------------------------
SELECT table_name AS "Tablas creadas"
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('clientes','direcciones','datos_fiscales')
ORDER BY table_name;