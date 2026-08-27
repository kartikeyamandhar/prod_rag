"""Replayer transaction reality (audit A11): ticks must commit as they land.

Integration test; skipped without DATABASE_URL. Pre-fix, load_state left the
connection inside an implicit transaction, so every apply_commit ran as a
SAVEPOINT of one giant transaction: 143 live-applied commits were invisible to
other connections and a crash would have rolled back all of them.
"""

from __future__ import annotations

import os

import psycopg
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs local DB (run with --env-file .env)"
)


def test_load_state_leaves_connection_idle() -> None:
    from updater.replayer import SCHEMA, load_state

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(SCHEMA)
        conn.commit()
        current = load_state(conn, start_sha="test-sha-a11")
        assert current
        # IDLE means the next conn.transaction() is a REAL transaction.
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


def test_work_after_load_state_commits_per_transaction_block() -> None:
    from updater.replayer import SCHEMA, load_state

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(SCHEMA)
        conn.commit()
        load_state(conn, start_sha="test-sha-a11")
        with conn.transaction():
            conn.execute(
                "INSERT INTO replay_ledger (commit_sha, commit_date, page_path,"
                " change_type, old_path, chunks_before, chunks_after)"
                " VALUES ('test:a11', now(), 'test/a11.md', 'modified', NULL, 1, 1)"
            )
        # The block above must be durable NOW, from a second connection,
        # even though this connection never calls conn.commit() again.
        with psycopg.connect(os.environ["DATABASE_URL"]) as other:
            with other.cursor() as cur:
                cur.execute("SELECT count(*) FROM replay_ledger WHERE commit_sha = 'test:a11'")
                fetched = cur.fetchone()
                assert fetched is not None and fetched[0] == 1
    # Cleanup with a fresh connection (outlives the assertion under test).
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as cleanup:
        cleanup.execute("DELETE FROM replay_ledger WHERE commit_sha = 'test:a11'")
