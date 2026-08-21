"""Incident 3: orphaned page. SCRIPTED deletion, labeled as such in the ledger.

Mirrors an upstream page removal: measure what retrieval cites for a query before
the deletion lands in the KB (the failure window: the system keeps recommending a
removed page), apply the deletion through the replayer's own delete path with a
ledger row, then measure again.

Run: uv run --env-file .env python -m probes.incident3_orphan
"""

from __future__ import annotations

import datetime as dt
import json
import os

import psycopg

from retrieval.embedder import embed_query, get_query_embedder
from updater.replayer import _delete_page

TARGET = "content/en/docs/concepts/services-networking/ingress.md"
QUERY = "ingress path routing rules for services"


def top5(cur, qvec) -> list[str]:
    cur.execute(
        "SELECT p.path FROM chunks c JOIN pages p ON p.id = c.page_id"
        " ORDER BY c.embedding <=> %s::vector LIMIT 5",
        (qvec,),
    )
    return [r[0] for r in cur.fetchall()]


def main() -> None:
    embedder = get_query_embedder(os.environ["EMBED_MODEL_NAME"])
    qvec = embed_query(embedder, QUERY)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            before = top5(cur, qvec)
        with conn.transaction():
            with conn.cursor() as cur:
                chunks_removed = _delete_page(cur, TARGET)
                cur.execute(
                    "INSERT INTO replay_ledger (commit_sha, commit_date, page_path,"
                    " change_type, old_path, chunks_before, chunks_after)"
                    " VALUES (%s, %s, %s, 'deleted', NULL, %s, 0)",
                    (
                        "scripted:incident-3",
                        dt.datetime.now(dt.UTC),
                        TARGET,
                        chunks_removed,
                    ),
                )
        with conn.cursor() as cur:
            after = top5(cur, qvec)
            cur.execute(
                "SELECT commit_sha, change_type, chunks_before FROM replay_ledger"
                " WHERE commit_sha = 'scripted:incident-3'"
            )
            ledger = cur.fetchall()

    report = {
        "incident": 3,
        "scripted": True,
        "deleted_page": TARGET,
        "query": QUERY,
        "top5_before": before,
        "target_in_top5_before": TARGET in before,
        "chunks_removed": chunks_removed,
        "top5_after": after,
        "target_in_top5_after": TARGET in after,
        "ledger": [list(row) for row in ledger],
    }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
