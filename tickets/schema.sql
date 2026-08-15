-- Ticket corpus schema, Phase 1b. Held-out tickets carry NULL embeddings so vector
-- search can never surface them; the is_held_out flag is defense in depth for FTS.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tickets (
    number BIGINT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    labels TEXT[] NOT NULL DEFAULT '{}',
    sigs TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    url TEXT NOT NULL,
    closing_pr_number BIGINT,
    closing_pr_url TEXT,
    tenant_id INT NOT NULL,
    is_held_out BOOLEAN NOT NULL,
    has_images BOOLEAN NOT NULL,
    snapshot_date DATE NOT NULL,
    embed_text TEXT NOT NULL,
    embedding vector(384),
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', left(coalesce(title, '') || ' ' || coalesce(body, ''), 200000))
    ) STORED
);

CREATE INDEX IF NOT EXISTS tickets_embedding_hnsw
    ON tickets USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS tickets_tsv_gin
    ON tickets USING gin (tsv);
CREATE INDEX IF NOT EXISTS tickets_tenant_idx
    ON tickets (tenant_id);
