-- CIVITAS-RAG — Initialisation PostgreSQL
-- Exécuté automatiquement au premier démarrage du container

-- Extension pgvector
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Schéma applicatif
CREATE SCHEMA IF NOT EXISTS civitas;

-- Log d'ingestion (mirror du tracker SQLite, pour audit)
CREATE TABLE IF NOT EXISTS civitas.ingestion_log (
    id            BIGSERIAL PRIMARY KEY,
    file_path     TEXT        NOT NULL,
    collection    TEXT        NOT NULL,
    file_hash     TEXT        NOT NULL DEFAULT '',
    status        TEXT        NOT NULL DEFAULT 'success'
                              CHECK(status IN ('success','failed','skipped')),
    chunks_count  INTEGER     NOT NULL DEFAULT 0,
    file_size     BIGINT      NOT NULL DEFAULT 0,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    error_msg     TEXT,
    UNIQUE(file_path, collection)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_log_collection
    ON civitas.ingestion_log(collection);
CREATE INDEX IF NOT EXISTS idx_ingestion_log_status
    ON civitas.ingestion_log(status);

COMMENT ON TABLE civitas.ingestion_log IS
    'Miroir du tracker SQLite — audit centralisé des ingestions';
