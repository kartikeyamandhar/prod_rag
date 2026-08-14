-- Bootstrap schema, Phase 1a scope: docs pages and chunks with vector + FTS indexes.
-- Later phases append tickets, tenants, replay ledger, and probe results here.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS pages (
    id BIGSERIAL PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    corpus_sha TEXT NOT NULL,
    has_images BOOLEAN NOT NULL DEFAULT FALSE,
    n_chunks INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id BIGSERIAL PRIMARY KEY,
    page_id BIGINT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    section TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    n_chars INT NOT NULL,
    embedding vector(384) NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (page_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin
    ON chunks USING gin (tsv);
