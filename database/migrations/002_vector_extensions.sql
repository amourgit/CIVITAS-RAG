-- ═══════════════════════════════════════════════════════════════════════════
--  CIVITAS — Migration 002: pgvector Extension + Hybrid Search Tables
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

-- Install required PostgreSQL extensions
CREATE EXTENSION IF NOT EXISTS vector;           -- pgvector
CREATE EXTENSION IF NOT EXISTS pg_trgm;          -- trigram similarity
CREATE EXTENSION IF NOT EXISTS unaccent;          -- accent-insensitive search
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";       -- UUID generation

-- ─────────────────────────────────────────────────────────────────────────
--  GLOBAL VECTOR TABLE (fallback / global-shared space)
--  Space-specific tables are created dynamically per KnowledgeSpace.
--  See civitas.storage.vector.pgvector.PgVectorSchema.create_vector_table()
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_vectors_global (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id     TEXT        NOT NULL UNIQUE,
    text        TEXT        NOT NULL,
    metadata    JSONB       NOT NULL DEFAULT '{}',
    embedding   VECTOR(1536),                    -- Override dimension via env var
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_civitas_vectors_global_metadata
    ON civitas_vectors_global USING GIN (metadata);

CREATE INDEX IF NOT EXISTS idx_civitas_vectors_global_fts
    ON civitas_vectors_global
    USING GIN (to_tsvector('english', text));

-- HNSW index (best for most use cases — fast approximate ANN)
CREATE INDEX IF NOT EXISTS idx_civitas_vectors_global_hnsw
    ON civitas_vectors_global
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ─────────────────────────────────────────────────────────────────────────
--  EMBEDDING CACHE (avoid re-embedding identical content)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_embedding_cache (
    content_hash    TEXT        PRIMARY KEY,
    model           TEXT        NOT NULL,
    dimensions      INTEGER     NOT NULL,
    embedding       VECTOR(1536),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_civitas_embedding_cache_model ON civitas_embedding_cache (model);

COMMIT;
