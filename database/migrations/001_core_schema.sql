-- ═══════════════════════════════════════════════════════════════════════════
--  CIVITAS — Migration 001: Core Schema
--  Tables: documents, knowledge_spaces, taxonomy_nodes, document_chunks
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────
--  KNOWLEDGE SPACES
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_knowledge_spaces (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    TEXT        NOT NULL UNIQUE,
    display_name            TEXT        NOT NULL,
    slug                    TEXT        NOT NULL UNIQUE,
    description             TEXT,
    space_type              TEXT        NOT NULL DEFAULT 'domain',
    domain                  TEXT,
    status                  TEXT        NOT NULL DEFAULT 'provisioning',
    owner_id                TEXT        NOT NULL,
    owner_team_id           TEXT,
    read_teams              JSONB       NOT NULL DEFAULT '[]',
    write_teams             JSONB       NOT NULL DEFAULT '[]',
    read_roles              JSONB       NOT NULL DEFAULT '[]',
    read_agent_ids          JSONB       NOT NULL DEFAULT '[]',
    admin_user_ids          JSONB       NOT NULL DEFAULT '[]',
    config                  JSONB       NOT NULL DEFAULT '{}',
    tags                    JSONB       NOT NULL DEFAULT '[]',
    document_count          INTEGER     NOT NULL DEFAULT 0,
    chunk_count             INTEGER     NOT NULL DEFAULT 0,
    total_queries           INTEGER     NOT NULL DEFAULT 0,
    last_ingestion_at       TIMESTAMPTZ,
    last_query_at           TIMESTAMPTZ,
    custom_fields           JSONB       NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_civitas_knowledge_spaces_status  ON civitas_knowledge_spaces (status);
CREATE INDEX idx_civitas_knowledge_spaces_domain   ON civitas_knowledge_spaces (domain);
CREATE INDEX idx_civitas_knowledge_spaces_tags     ON civitas_knowledge_spaces USING GIN (tags);

-- ─────────────────────────────────────────────────────────────────────────
--  TAXONOMY NODES
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_taxonomy_nodes (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT        NOT NULL,
    display_name    TEXT        NOT NULL,
    description     TEXT,
    parent_id       UUID        REFERENCES civitas_taxonomy_nodes(id) ON DELETE SET NULL,
    path            JSONB       NOT NULL DEFAULT '[]',
    full_path       TEXT        NOT NULL UNIQUE,
    depth           INTEGER     NOT NULL DEFAULT 0,
    is_leaf         BOOLEAN     NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    keywords        JSONB       NOT NULL DEFAULT '[]',
    examples        JSONB       NOT NULL DEFAULT '[]',
    document_count  INTEGER     NOT NULL DEFAULT 0,
    custom_fields   JSONB       NOT NULL DEFAULT '{}',
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_civitas_taxonomy_parent_id   ON civitas_taxonomy_nodes (parent_id);
CREATE INDEX idx_civitas_taxonomy_is_leaf      ON civitas_taxonomy_nodes (is_leaf);
CREATE INDEX idx_civitas_taxonomy_is_active    ON civitas_taxonomy_nodes (is_active);
CREATE INDEX idx_civitas_taxonomy_full_path    ON civitas_taxonomy_nodes (full_path text_pattern_ops);
CREATE INDEX idx_civitas_taxonomy_keywords     ON civitas_taxonomy_nodes USING GIN (keywords);

-- ─────────────────────────────────────────────────────────────────────────
--  DOCUMENTS
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_documents (
    id                  UUID        PRIMARY KEY,
    external_id         TEXT,
    title               TEXT        NOT NULL,
    slug                TEXT,
    version             INTEGER     NOT NULL DEFAULT 1,
    parent_version_id   UUID        REFERENCES civitas_documents(id) ON DELETE SET NULL,
    is_latest_version   BOOLEAN     NOT NULL DEFAULT TRUE,

    -- Content
    content             TEXT,
    content_preview     TEXT,
    raw_content_path    TEXT,
    checksum            TEXT,

    -- Format
    format              TEXT        NOT NULL DEFAULT 'unknown',
    language            TEXT        NOT NULL DEFAULT 'unknown',
    encoding            TEXT        NOT NULL DEFAULT 'utf-8',
    byte_size           BIGINT,
    page_count          INTEGER,
    word_count          INTEGER,
    character_count     INTEGER,

    -- Metadata (full JSON)
    metadata            JSONB       NOT NULL DEFAULT '{}',

    -- Relationships
    chunk_ids           JSONB       NOT NULL DEFAULT '[]',

    -- Timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    indexed_at          TIMESTAMPTZ
);

-- Primary access patterns
CREATE INDEX idx_civitas_docs_external_id        ON civitas_documents (external_id) WHERE external_id IS NOT NULL;
CREATE INDEX idx_civitas_docs_is_latest          ON civitas_documents (is_latest_version);
CREATE INDEX idx_civitas_docs_checksum           ON civitas_documents (checksum) WHERE checksum IS NOT NULL;

-- Metadata JSON indexes for common filter paths
CREATE INDEX idx_civitas_docs_space_id           ON civitas_documents ((metadata->>'knowledge_space_id'));
CREATE INDEX idx_civitas_docs_domain             ON civitas_documents ((metadata->>'domain'));
CREATE INDEX idx_civitas_docs_category           ON civitas_documents ((metadata->>'category'));
CREATE INDEX idx_civitas_docs_lifecycle_state    ON civitas_documents ((metadata->>'lifecycle_state'));
CREATE INDEX idx_civitas_docs_taxonomy_path      ON civitas_documents ((metadata->>'taxonomy_path'));
CREATE INDEX idx_civitas_docs_owner_id           ON civitas_documents ((metadata->'ownership'->>'owner_id'));
CREATE INDEX idx_civitas_docs_team_id            ON civitas_documents ((metadata->'ownership'->>'team_id'));
CREATE INDEX idx_civitas_docs_access_level       ON civitas_documents ((metadata->'access_control'->>'access_level'));
CREATE INDEX idx_civitas_docs_quality_score      ON civitas_documents (((metadata->'quality'->>'composite_score')::FLOAT));
CREATE INDEX idx_civitas_docs_created_at         ON civitas_documents (created_at DESC);
CREATE INDEX idx_civitas_docs_indexed_at         ON civitas_documents (indexed_at DESC);

-- Full-text search on title and content_preview
CREATE INDEX idx_civitas_docs_fts
    ON civitas_documents
    USING GIN (to_tsvector('english', title || ' ' || COALESCE(content_preview, '')));

-- Tags GIN index
CREATE INDEX idx_civitas_docs_tags
    ON civitas_documents USING GIN ((metadata->'tags'));

-- ─────────────────────────────────────────────────────────────────────────
--  DOCUMENT CHUNKS  (non-vector metadata — vectors in civitas_vectors_* tables)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_document_chunks (
    id                  UUID        PRIMARY KEY,
    document_id         UUID        NOT NULL REFERENCES civitas_documents(id) ON DELETE CASCADE,
    document_title      TEXT        NOT NULL,
    document_version    INTEGER     NOT NULL DEFAULT 1,
    chunk_index         INTEGER     NOT NULL,
    chunk_type          TEXT        NOT NULL DEFAULT 'semantic',
    start_char          INTEGER,
    end_char            INTEGER,
    page_number         INTEGER,
    section_heading     TEXT,
    content             TEXT        NOT NULL,
    content_hash        TEXT        NOT NULL,
    token_count         INTEGER,
    prev_chunk_id       UUID        REFERENCES civitas_document_chunks(id) ON DELETE SET NULL,
    next_chunk_id       UUID        REFERENCES civitas_document_chunks(id) ON DELETE SET NULL,

    -- Inherited metadata (denormalized for retrieval performance)
    domain              TEXT        NOT NULL,
    subdomain           TEXT,
    category            TEXT        NOT NULL,
    knowledge_space_id  UUID,
    knowledge_space_name TEXT,
    taxonomy_path       TEXT,
    tags                JSONB       NOT NULL DEFAULT '[]',
    classification_level TEXT       NOT NULL DEFAULT 'internal',
    access_level        TEXT        NOT NULL DEFAULT 'team',
    allowed_teams       JSONB       NOT NULL DEFAULT '[]',
    allowed_roles       JSONB       NOT NULL DEFAULT '[]',
    allowed_agent_ids   JSONB       NOT NULL DEFAULT '[]',

    -- Embedding info
    embedding_model     TEXT,
    embedded_at         TIMESTAMPTZ,
    quality_score       FLOAT,
    extra_metadata      JSONB       NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (document_id, chunk_index)
);

CREATE INDEX idx_civitas_chunks_document_id     ON civitas_document_chunks (document_id);
CREATE INDEX idx_civitas_chunks_space_id        ON civitas_document_chunks (knowledge_space_id);
CREATE INDEX idx_civitas_chunks_domain          ON civitas_document_chunks (domain);
CREATE INDEX idx_civitas_chunks_content_hash    ON civitas_document_chunks (content_hash);
CREATE INDEX idx_civitas_chunks_tags            ON civitas_document_chunks USING GIN (tags);
CREATE INDEX idx_civitas_chunks_allowed_teams   ON civitas_document_chunks USING GIN (allowed_teams);
CREATE INDEX idx_civitas_chunks_fts
    ON civitas_document_chunks
    USING GIN (to_tsvector('english', content));

-- ─────────────────────────────────────────────────────────────────────────
--  INDEX REGISTRY
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_index_registry (
    index_id                TEXT        PRIMARY KEY,
    index_type              TEXT        NOT NULL,
    knowledge_space_id      TEXT,
    knowledge_space_name    TEXT,
    status                  TEXT        NOT NULL DEFAULT 'uninitialized',
    document_count          INTEGER     NOT NULL DEFAULT 0,
    node_count              INTEGER     NOT NULL DEFAULT 0,
    embedding_model         TEXT,
    embedding_dimensions    INTEGER,
    similarity_metric       TEXT,
    vector_table_name       TEXT,
    extra                   JSONB       NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    built_at                TIMESTAMPTZ,
    last_updated_at         TIMESTAMPTZ
);

CREATE INDEX idx_civitas_index_registry_space   ON civitas_index_registry (knowledge_space_id);
CREATE INDEX idx_civitas_index_registry_type    ON civitas_index_registry (index_type);

COMMIT;
