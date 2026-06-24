-- ═══════════════════════════════════════════════════════════════════════════
--  CIVITAS — Migration 003: GraphRAG Schema
--  Tables: entities, relations, communities, community_reports
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────
--  ENTITIES
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_graph_entities (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_space_name TEXT       NOT NULL,
    name                TEXT        NOT NULL,
    entity_type         TEXT        NOT NULL DEFAULT 'CONCEPT',
    description         TEXT,
    aliases             JSONB       NOT NULL DEFAULT '[]',
    source_chunk_ids    JSONB       NOT NULL DEFAULT '[]',
    confidence          FLOAT       NOT NULL DEFAULT 1.0,
    occurrence_count    INTEGER     NOT NULL DEFAULT 1,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (knowledge_space_name, name)
);

CREATE INDEX idx_civitas_entities_space     ON civitas_graph_entities (knowledge_space_name);
CREATE INDEX idx_civitas_entities_type      ON civitas_graph_entities (entity_type);
CREATE INDEX idx_civitas_entities_name_fts
    ON civitas_graph_entities
    USING GIN (to_tsvector('english', name || ' ' || COALESCE(description, '')));

-- ─────────────────────────────────────────────────────────────────────────
--  RELATIONS
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_graph_relations (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_space_name TEXT       NOT NULL,
    source_entity       TEXT        NOT NULL,
    target_entity       TEXT        NOT NULL,
    relation_type       TEXT        NOT NULL,
    description         TEXT,
    weight              FLOAT       NOT NULL DEFAULT 1.0,
    source_chunk_id     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_civitas_relations_space    ON civitas_graph_relations (knowledge_space_name);
CREATE INDEX idx_civitas_relations_source   ON civitas_graph_relations (source_entity);
CREATE INDEX idx_civitas_relations_target   ON civitas_graph_relations (target_entity);
CREATE INDEX idx_civitas_relations_type     ON civitas_graph_relations (relation_type);

-- ─────────────────────────────────────────────────────────────────────────
--  COMMUNITIES
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_graph_communities (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_space_name TEXT       NOT NULL,
    community_id        INTEGER     NOT NULL,
    title               TEXT,
    summary             TEXT,
    level               INTEGER     NOT NULL DEFAULT 0,
    weight              FLOAT       NOT NULL DEFAULT 1.0,
    entities            JSONB       NOT NULL DEFAULT '[]',
    source_chunk_ids    JSONB       NOT NULL DEFAULT '[]',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (knowledge_space_name, community_id)
);

CREATE INDEX idx_civitas_communities_space  ON civitas_graph_communities (knowledge_space_name);
CREATE INDEX idx_civitas_communities_fts
    ON civitas_graph_communities
    USING GIN (to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(summary, '')));

COMMIT;
