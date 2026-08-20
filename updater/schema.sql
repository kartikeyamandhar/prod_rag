-- Replay machinery: applied-commit state plus a ledger of every page change.
-- The ledger is what incidents 2 and 3 read to prove staleness and orphan windows.

CREATE TABLE IF NOT EXISTS replay_state (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    current_sha TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS replay_ledger (
    id BIGSERIAL PRIMARY KEY,
    commit_sha TEXT NOT NULL,
    commit_date TIMESTAMPTZ NOT NULL,
    page_path TEXT NOT NULL,
    change_type TEXT NOT NULL CHECK (change_type IN ('added', 'modified', 'deleted', 'renamed')),
    old_path TEXT,
    chunks_before INT NOT NULL,
    chunks_after INT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
