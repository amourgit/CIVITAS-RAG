-- ═══════════════════════════════════════════════════════════════════════════
--  CIVITAS — Migration 004: Governance & Audit Schema
--  Tables: audit_events, retention_policies, access_policies, quality_reports
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────
--  AUDIT EVENTS  (append-only — never updated)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_audit_events (
    event_id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type              TEXT        NOT NULL,
    occurred_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Principal
    actor_id                TEXT        NOT NULL,
    actor_type              TEXT        NOT NULL DEFAULT 'system',

    -- Resource
    resource_type           TEXT        NOT NULL,
    resource_id             TEXT,
    resource_name           TEXT,

    -- Context
    knowledge_space_id      TEXT,
    knowledge_space_name    TEXT,

    -- Payload
    payload                 JSONB       NOT NULL DEFAULT '{}',
    message                 TEXT,
    success                 BOOLEAN     NOT NULL DEFAULT TRUE,
    error_message           TEXT,
    correlation_id          TEXT,
    session_id              TEXT
);

-- Audit events are append-only and queried by time range + actor + resource
CREATE INDEX idx_civitas_audit_occurred_at      ON civitas_audit_events (occurred_at DESC);
CREATE INDEX idx_civitas_audit_event_type       ON civitas_audit_events (event_type);
CREATE INDEX idx_civitas_audit_actor_id         ON civitas_audit_events (actor_id);
CREATE INDEX idx_civitas_audit_resource         ON civitas_audit_events (resource_type, resource_id);
CREATE INDEX idx_civitas_audit_space            ON civitas_audit_events (knowledge_space_name);
CREATE INDEX idx_civitas_audit_success          ON civitas_audit_events (success);
CREATE INDEX idx_civitas_audit_correlation      ON civitas_audit_events (correlation_id) WHERE correlation_id IS NOT NULL;

-- Partition hint: in high-volume production, partition by occurred_at month
-- ALTER TABLE civitas_audit_events PARTITION BY RANGE (occurred_at);

-- ─────────────────────────────────────────────────────────────────────────
--  QUALITY REPORTS
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_quality_reports (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID        NOT NULL REFERENCES civitas_documents(id) ON DELETE CASCADE,
    document_title      TEXT        NOT NULL,
    composite_score     FLOAT,
    disposition         TEXT        NOT NULL DEFAULT 'review',
    rule_results        JSONB       NOT NULL DEFAULT '[]',
    issues              JSONB       NOT NULL DEFAULT '[]',
    checked_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checked_by          TEXT        NOT NULL DEFAULT 'system'
);

CREATE INDEX idx_civitas_quality_document_id    ON civitas_quality_reports (document_id);
CREATE INDEX idx_civitas_quality_score          ON civitas_quality_reports (composite_score);
CREATE INDEX idx_civitas_quality_disposition    ON civitas_quality_reports (disposition);
CREATE INDEX idx_civitas_quality_checked_at     ON civitas_quality_reports (checked_at DESC);

-- ─────────────────────────────────────────────────────────────────────────
--  RETENTION POLICIES
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS civitas_retention_policies (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT        NOT NULL UNIQUE,
    description     TEXT,
    retention_days  INTEGER,         -- NULL = permanent
    applies_to      JSONB NOT NULL DEFAULT '{}',  -- {domains: [], categories: [], spaces: []}
    action_on_expire TEXT NOT NULL DEFAULT 'archive',  -- archive | purge | notify
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO civitas_retention_policies (name, description, retention_days, action_on_expire)
VALUES
    ('short_term',  'Short-term: 90 days',        90,   'archive'),
    ('standard',    'Standard: 1 year',           365,  'archive'),
    ('long_term',   'Long-term: 5 years',         1825, 'archive'),
    ('permanent',   'Permanent: never expires',   NULL, 'notify'),
    ('regulatory',  'Regulatory: defined by compliance team', NULL, 'notify')
ON CONFLICT (name) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────
--  USEFUL VIEWS
-- ─────────────────────────────────────────────────────────────────────────

-- Document summary view (no content field for performance)
CREATE OR REPLACE VIEW civitas_documents_summary AS
SELECT
    id,
    title,
    version,
    is_latest_version,
    format,
    language,
    word_count,
    metadata->>'domain'                                         AS domain,
    metadata->>'category'                                       AS category,
    metadata->>'knowledge_space_name'                           AS knowledge_space,
    metadata->>'lifecycle_state'                                AS lifecycle_state,
    (metadata->'quality'->>'composite_score')::FLOAT            AS quality_score,
    metadata->'access_control'->>'classification_level'         AS classification_level,
    metadata->'ownership'->>'owner_id'                          AS owner_id,
    metadata->'ownership'->>'team_id'                           AS team_id,
    created_at,
    updated_at,
    indexed_at
FROM civitas_documents
WHERE is_latest_version = TRUE;

-- Recent audit events view
CREATE OR REPLACE VIEW civitas_recent_audit AS
SELECT
    event_id,
    event_type,
    occurred_at,
    actor_id,
    actor_type,
    resource_type,
    resource_id,
    knowledge_space_name,
    success,
    error_message
FROM civitas_audit_events
ORDER BY occurred_at DESC
LIMIT 1000;

-- Knowledge space stats view
CREATE OR REPLACE VIEW civitas_space_stats AS
SELECT
    ks.name,
    ks.display_name,
    ks.status,
    ks.document_count,
    ks.chunk_count,
    ks.total_queries,
    ks.last_ingestion_at,
    ks.last_query_at,
    COUNT(DISTINCT d.id) AS actual_document_count
FROM civitas_knowledge_spaces ks
LEFT JOIN civitas_documents d
    ON d.metadata->>'knowledge_space_id' = ks.id::TEXT
    AND d.is_latest_version = TRUE
GROUP BY ks.id, ks.name, ks.display_name, ks.status,
         ks.document_count, ks.chunk_count, ks.total_queries,
         ks.last_ingestion_at, ks.last_query_at;

COMMIT;
