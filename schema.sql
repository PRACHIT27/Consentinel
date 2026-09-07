-- Consentinel schema. FROZEN CONTRACT — do not change without agreeing with your teammate.
-- SQLite dialect. Portable enough to move to Postgres/ClickHouse later.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- performers: the people whose name, image and likeness we protect.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS performers (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    aliases           TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings
    reference_images  TEXT NOT NULL DEFAULT '[]',   -- JSON array of URIs (for reverse-image sweep)
    notes             TEXT,
    created_at        TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- consents: the permission slips. The spine of the whole product.
-- Both pipelines resolve every question against this table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS consents (
    id                    TEXT PRIMARY KEY,
    performer_id          TEXT NOT NULL REFERENCES performers(id),
    licensee              TEXT NOT NULL,              -- who was granted the right
    source_doc_ref        TEXT,                       -- URI of the contract we extracted from
    permitted_uses        TEXT NOT NULL DEFAULT '[]', -- JSON array: voice_synth|face_replace|full_replica|archival_reuse
    territories           TEXT NOT NULL DEFAULT '[]', -- JSON array of ISO 3166-1 alpha-2, or ["WORLDWIDE"]
    valid_from            TEXT,
    valid_to              TEXT,
    compensation_trigger  TEXT,                       -- free text; what triggers a payment
    clause_citations      TEXT NOT NULL DEFAULT '[]', -- JSON array of {quote, page}
    created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_consents_performer ON consents(performer_id);

-- ---------------------------------------------------------------------------
-- findings: outward pipeline. Suspected third-party use of a likeness.
-- url_hash gives idempotency so re-running a sweep does not duplicate rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS findings (
    id                  TEXT PRIMARY KEY,
    performer_id        TEXT NOT NULL REFERENCES performers(id),
    url                 TEXT NOT NULL,
    url_hash            TEXT NOT NULL UNIQUE,
    discovered_via      TEXT NOT NULL,              -- text|image
    discovered_locale   TEXT NOT NULL,              -- where we searched FROM, e.g. pt-BR
    target_territories  TEXT NOT NULL DEFAULT '[]', -- where the offering is AIMED (currency, shipping, jurisdiction)
    modality            TEXT,                       -- voice|face|performance
    is_commercial       INTEGER,                    -- 0/1
    evidence_quote      TEXT,                       -- the sentence that proves it. Required for any verdict.
    confidence          REAL,
    verdict             TEXT,                       -- authorized|unauthorized|ambiguous
    matched_consent_id  TEXT REFERENCES consents(id),
    reasoning           TEXT,
    status              TEXT NOT NULL DEFAULT 'new',-- new|reviewed|dossier_drafted|dismissed
    evidence_uri        TEXT,                       -- immutable snapshot in the evidence store
    first_seen          TEXT NOT NULL,
    last_checked        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_findings_performer ON findings(performer_id);
CREATE INDEX IF NOT EXISTS idx_findings_verdict   ON findings(verdict, status);

-- ---------------------------------------------------------------------------
-- assets: inward pipeline. Our own deliverables.
-- Default clearance_state is 'unverified'. An asset only becomes 'cleared'
-- when a consent row covers what was done to it. We prove coverage, not AI.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assets (
    id                   TEXT PRIMARY KEY,
    production_id        TEXT NOT NULL,
    shot_code            TEXT,
    filename             TEXT NOT NULL,
    content_hash         TEXT,
    provenance_metadata  TEXT,                        -- JSON; embedded Content Credentials if present
    vendor               TEXT,
    invoice_ref          TEXT,
    performer_id         TEXT REFERENCES performers(id),
    synthetic            TEXT NOT NULL DEFAULT 'unknown', -- yes|no|unknown
    detected_modality    TEXT,
    clearance_state      TEXT NOT NULL DEFAULT 'unverified', -- unverified|cleared|blocked
    matched_consent_id   TEXT REFERENCES consents(id),
    reasoning            TEXT,
    created_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assets_production ON assets(production_id, clearance_state);

-- ---------------------------------------------------------------------------
-- dossiers: the enforcement deliverable. Drafted, never auto-sent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dossiers (
    id               TEXT PRIMARY KEY,
    finding_id       TEXT NOT NULL REFERENCES findings(id),
    evidence_bundle  TEXT NOT NULL,   -- JSON: snapshot URIs, quotes, matched clauses
    draft_notice     TEXT NOT NULL,   -- takedown text for a human to review and send
    generated_at     TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- audit_log: append-only. For a compliance product the defensible trail IS
-- the value. Every agent decision lands here, including the age of any cached
-- input it relied on.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id             TEXT PRIMARY KEY,
    ts             TEXT NOT NULL,
    actor          TEXT NOT NULL,   -- agent or tool name
    subject_type   TEXT,            -- finding|asset|consent|performer
    subject_id     TEXT,
    inputs         TEXT,            -- JSON
    tool_calls     TEXT,            -- JSON: [{tool, args, from_cache, cache_age_s}]
    output         TEXT,            -- JSON
    prompt_version TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_subject ON audit_log(subject_type, subject_id);
