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
CREATE TABLE IF NOT EXISTS endpoint0.lab_faults (
    event_id UUID PRIMARY KEY REFERENCES endpoint0.outbox(event_id),
    step VARCHAR(32) NOT NULL,
    remaining INTEGER NOT NULL,
    permanent BOOLEAN NOT NULL DEFAULT FALSE
);
