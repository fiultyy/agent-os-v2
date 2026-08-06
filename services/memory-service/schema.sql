-- mem-service KG schema (ADR-2 storage, ADR-3 Fact reification)
-- No MemoryItem table — Fact reification is self-contained (per ADR-2 Decision).
-- Fact.value is the content carrier; object_id is nullable (unary/literal facts).

PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS entity (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    properties  TEXT NOT NULL DEFAULT '{}',   -- JSON object
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entity_name ON entity(name);
CREATE INDEX IF NOT EXISTS idx_entity_type ON entity(entity_type);

CREATE TABLE IF NOT EXISTS fact (
    id            TEXT PRIMARY KEY,
    subject_id    TEXT NOT NULL,
    predicate     TEXT NOT NULL,
    object_id     TEXT,                          -- nullable: literal facts carry value only
    value         TEXT,                          -- content carrier (ADR-3)
    valid_from    TEXT,
    valid_to      TEXT,
    fact_type     TEXT NOT NULL DEFAULT 'stable', -- ephemeral|stable|permanent
    LIF           REAL NOT NULL DEFAULT 0.5,      -- trust scalar (NOT NeuralField — ADR-4); decayed in place
    original_lif  REAL NOT NULL DEFAULT 0.5,      -- frozen LIF at store time — decay rebases from this (idempotent, ADR-8)
    confidence    REAL NOT NULL DEFAULT 0.5,
    source_refs   TEXT NOT NULL DEFAULT '[]',     -- JSON array: raw sessionId/leafUuid
    extractor     TEXT NOT NULL DEFAULT 'regex',
    status        TEXT NOT NULL DEFAULT 'active', -- active|deprecated|superseded
    supersedes_id TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (subject_id) REFERENCES entity(id),
    FOREIGN KEY (object_id)  REFERENCES entity(id),
    FOREIGN KEY (supersedes_id) REFERENCES fact(id)
);
CREATE INDEX IF NOT EXISTS idx_fact_subject ON fact(subject_id);
CREATE INDEX IF NOT EXISTS idx_fact_object  ON fact(object_id);
CREATE INDEX IF NOT EXISTS idx_fact_pred    ON fact(predicate);
CREATE INDEX IF NOT EXISTS idx_fact_status  ON fact(status);
