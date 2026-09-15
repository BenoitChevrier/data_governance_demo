-- =============================================================================
-- postgres-immo — initial schema layout
--
-- The official PostgreSQL entrypoint executes every .sql and .sh file found in
-- /docker-entrypoint-initdb.d, in filename order, but ONLY on the very first
-- start of an empty data volume. Once $PGDATA/PG_VERSION exists, the whole
-- initialisation block is skipped.
--
-- Practical consequence: editing this file does nothing on an existing volume.
-- To replay it:  docker compose down -v && docker compose up -d
-- =============================================================================

-- The medallion layers are schemas of a single database, not separate
-- databases. bronze is written by the loader; silver and gold are built by dbt,
-- which creates them on its own — they are declared here so that the catalog's
-- schema filter has something to match from the first run, and so that the
-- layout of the warehouse is readable before any data lands.
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

COMMENT ON SCHEMA bronze IS 'Raw layer: open data as extracted, defects included.';
COMMENT ON SCHEMA silver IS 'Cleansed layer: typed, deduplicated, normalised. Built by dbt.';
COMMENT ON SCHEMA gold   IS 'Serving layer: KPIs exposed to the catalog. Built by dbt.';

-- Section below commented out on 2026-09-15  because it is only a temporary smoke test for Sprint 0. 

-- -----------------------------------------------------------------------------
-- Sprint 0 smoke test.
--
-- Its only purpose is to prove that the OpenMetadata PostgreSQL connector
-- reaches this server and lands metadata in the catalog. It carries a primary
-- key, a NOT NULL column, a numeric and a timestamp so that the ingested
-- description is not trivially empty.
--
-- To be dropped in Sprint 1, once the real bronze tables exist.
-- -----------------------------------------------------------------------------
-- -- CREATE TABLE IF NOT EXISTS bronze.connectivity_check (
--     id          integer PRIMARY KEY,
--     label       text        NOT NULL,
--     surface_m2  numeric(10,2),
--     checked_at  timestamptz NOT NULL DEFAULT now()
-- );

-- COMMENT ON TABLE bronze.connectivity_check IS
--     'Sprint 0 smoke test for the PostgreSQL metadata connector. Temporary.';

-- INSERT INTO bronze.connectivity_check (id, label, surface_m2) VALUES
--     (1, 'smoke test row A', 1882.00),
--     (2, 'smoke test row B',  742.50)
-- ON CONFLICT (id) DO NOTHING;
