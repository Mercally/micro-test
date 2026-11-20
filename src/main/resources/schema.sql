CREATE SCHEMA IF NOT EXISTS endpoint0;
CREATE SCHEMA IF NOT EXISTS endpoint1;
CREATE SCHEMA IF NOT EXISTS endpoint2;
CREATE SCHEMA IF NOT EXISTS endpoint3;
CREATE SCHEMA IF NOT EXISTS endpoint4;
CREATE SCHEMA IF NOT EXISTS endpoint5;

CREATE TABLE IF NOT EXISTS endpoint0.sales (
    id UUID PRIMARY KEY,
    external_reference VARCHAR(120) NOT NULL UNIQUE,
    customer_name VARCHAR(200) NOT NULL,
    customer_email VARCHAR(320) NOT NULL,
    product_code VARCHAR(80) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS endpoint1.documents (
    id UUID PRIMARY KEY,
    sale_id UUID NOT NULL UNIQUE,
    file_name VARCHAR(200) NOT NULL,
    content BYTEA NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS endpoint2.policies (
    id UUID PRIMARY KEY,
    sale_id UUID NOT NULL UNIQUE,
    policy_number VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS endpoint3.uploads (
    id UUID PRIMARY KEY,
    sale_id UUID NOT NULL UNIQUE,
    document_id UUID NOT NULL,
    bucket VARCHAR(200) NOT NULL,
    object_key VARCHAR(500) NOT NULL,
    status VARCHAR(32) NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS endpoint4.policy_completions (
    id UUID PRIMARY KEY,
    sale_id UUID NOT NULL UNIQUE,
    policy_id UUID NOT NULL,
    status VARCHAR(32) NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS endpoint5.document_releases (
    id UUID PRIMARY KEY,
    sale_id UUID NOT NULL UNIQUE,
    document_id UUID NOT NULL,
    object_key VARCHAR(500) NOT NULL,
    download_url TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    released_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Additive migration: existing sales and documents remain intact.
ALTER TABLE endpoint0.sales ADD COLUMN IF NOT EXISTS generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE endpoint0.sales ADD COLUMN IF NOT EXISTS current_event_id UUID;
ALTER TABLE endpoint0.sales ADD COLUMN IF NOT EXISTS workflow_owner TEXT;
ALTER TABLE endpoint0.sales ADD COLUMN IF NOT EXISTS workflow_lease_until TIMESTAMPTZ;
ALTER TABLE endpoint0.sales ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE endpoint0.sales ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

CREATE TABLE IF NOT EXISTS endpoint0.outbox (
    event_id UUID PRIMARY KEY,
    sale_id UUID NOT NULL REFERENCES endpoint0.sales(id),
    generation INTEGER NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    version INTEGER NOT NULL CHECK (version = 1),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMPTZ,
    sqs_message_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    UNIQUE (sale_id, generation)
);
CREATE INDEX IF NOT EXISTS outbox_pending ON endpoint0.outbox(available_at, created_at)
    WHERE published_at IS NULL;

-- Durable, explicit lab faults. A separate transaction consumes each failed attempt.
-- AWS flow (N02): one row per completed step. Absence = not done. Steps check this before acting.
CREATE SCHEMA IF NOT EXISTS process;
CREATE TABLE IF NOT EXISTS process.steps (
    sale_id UUID NOT NULL REFERENCES endpoint0.sales(id),
    step VARCHAR(32) NOT NULL,
    reference TEXT NOT NULL,
    outcome VARCHAR(16) NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (sale_id, step)
);

-- Lab-only stand-ins for Content Manager and the insurer. Idempotent by sale.
CREATE SCHEMA IF NOT EXISTS mock;
CREATE TABLE IF NOT EXISTS mock.cm_documents (
    sale_id UUID PRIMARY KEY,
    document_id TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS mock.insurer_policies (
    sale_id UUID PRIMARY KEY,
    policy_number TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS endpoint0.lab_faults (
    event_id UUID PRIMARY KEY REFERENCES endpoint0.outbox(event_id),
    step VARCHAR(32) NOT NULL,
    remaining INTEGER NOT NULL,
    permanent BOOLEAN NOT NULL DEFAULT FALSE
);

-- AWS flow: one row per step attempt (RUNNING -> outcome). Read by GET /api/v1/purchases (UI tree).
CREATE TABLE IF NOT EXISTS process.history (
    id BIGSERIAL PRIMARY KEY,
    sale_id UUID NOT NULL,
    step VARCHAR(32) NOT NULL,
    outcome VARCHAR(32) NOT NULL,
    detail TEXT,
    delay_ms INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS history_sale ON process.history(sale_id, id);

-- Lab knob, read on every step call: random pause in [min,max] ms before document/cm/insurer.
-- Max 15000: the Step Functions HTTP task times out at 20 s.
CREATE TABLE IF NOT EXISTS process.lab_settings (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    step_delay_min_ms INTEGER NOT NULL DEFAULT 0,
    step_delay_max_ms INTEGER NOT NULL DEFAULT 10000,
    CHECK (step_delay_min_ms >= 0 AND step_delay_max_ms >= step_delay_min_ms AND step_delay_max_ms <= 15000)
);
INSERT INTO process.lab_settings(id) VALUES (TRUE) ON CONFLICT DO NOTHING;

-- Simulated work per business step (lab): CPU time burned (contention stretches wall time) and memory
-- held while the step runs. Columns added idempotently so existing databases upgrade on startup.
-- Defaults are non-zero: every business step does some work, like a real service would.
ALTER TABLE process.lab_settings ADD COLUMN IF NOT EXISTS step_cpu_ms INTEGER NOT NULL DEFAULT 100 CHECK (step_cpu_ms BETWEEN 0 AND 3000);
ALTER TABLE process.lab_settings ADD COLUMN IF NOT EXISTS step_memory_mb INTEGER NOT NULL DEFAULT 8 CHECK (step_memory_mb BETWEEN 0 AND 64);
ALTER TABLE process.history ADD COLUMN IF NOT EXISTS cpu_ms INTEGER NOT NULL DEFAULT 0;
ALTER TABLE process.history ADD COLUMN IF NOT EXISTS memory_mb INTEGER NOT NULL DEFAULT 0;
-- Lab document size: each generated PDF weighs a random size in [min,max] KB (incompressible attachment),
-- so document steps really hold and move it: create + sign + S3 upload ("document"); S3 download + CM
-- upload ("cm"). Default 200-500 KB, like the real signed document.
ALTER TABLE process.lab_settings ADD COLUMN IF NOT EXISTS document_min_kb INTEGER NOT NULL DEFAULT 200 CHECK (document_min_kb BETWEEN 0 AND 10240);
ALTER TABLE process.lab_settings ADD COLUMN IF NOT EXISTS document_max_kb INTEGER NOT NULL DEFAULT 500 CHECK (document_max_kb BETWEEN 0 AND 10240);
