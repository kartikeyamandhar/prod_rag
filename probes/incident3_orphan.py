"""Incident 3 v2: orphaned page. SCRIPTED deletion, labeled as such in the ledger.

Mirrors an upstream page removal: measure what retrieval serves for queries that
land on the page before the deletion reaches the KB (the failure window: the
system keeps recommending a removed page), apply the deletion through the
replayer's own delete path with a ledger row, then measure again. v2 runs the
pipeline's actual retrieval (hybrid_search, top-8) instead of raw vector top-5
(audit fix), stamps run_meta, and relies on `make reset-corpus` for restore
(v1 left the box corpus one page short for two further incidents, audit A13).

Run: uv run --env-file .env python -m probes.incident3_orphan
     (then: make reset-corpus)
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import psycopg

from probes.run_meta import run_meta
from retrieval.embedder import embed_query, get_query_embedder
from retrieval.search import hybrid_search

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "artifacts" / "incidents"

TARGET = "content/en/docs/concepts/services-networking/ingress.md"
# Customer-shaped queries that should land on the target page.
QUERIES = [
    "ingress path routing rules for services",
    "how do I route external HTTP traffic to different services by URL path",
    "ingress backend service not receiving traffic",
]
TENANT_ID = 1


def _serve(conn: psycopg.Connection, embedder, query: str) -> list[str]:
    qvec = embed_query(embedder, query)
    items = hybrid_search(conn, qvec, query, TENANT_ID)
    return [item.url for item in items if item.corpus == "docs"]


def main() -> None:
    from updater.replayer import _delete_page

    embedder = get_query_embedder(os.environ["EMBED_MODEL_NAME"])
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        before = {q: _serve(conn, embedder, q) for q in QUERIES}
        with conn.transaction():
            with conn.cursor() as cur:
                chunks_removed = _delete_page(cur, TARGET)
                cur.execute(
                    "INSERT INTO replay_ledger (commit_sha, commit_date, page_path,"
                    " change_type, old_path, chunks_before, chunks_after)"
                    " VALUES (%s, %s, %s, 'deleted', NULL, %s, 0)",
                    ("scripted:incident-3", dt.datetime.now(dt.UTC), TARGET, chunks_removed),
                )
        after = {q: _serve(conn, embedder, q) for q in QUERIES}
        with conn.cursor() as cur:
            cur.execute(
                "SELECT commit_sha, change_type, chunks_before FROM replay_ledger"
                " WHERE commit_sha = 'scripted:incident-3'"
            )
            ledger = [list(row) for row in cur.fetchall()]
        meta = run_meta(conn)

    report = {
        "incident": 3,
        "scripted": True,
        "deleted_page": TARGET,
        "chunks_removed": chunks_removed,
        "retrieval_path": "hybrid_search top-8, docs items",
        "queries": [
            {
                "query": q,
                "target_served_before": TARGET in before[q],
                "target_served_after": TARGET in after[q],
                "docs_before": before[q],
                "docs_after": after[q],
            }
            for q in QUERIES
        ],
        "ledger": ledger,
        "restore": "make reset-corpus (v1 never restored; audit A13)",
        "run_meta": meta,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "incident3_orphan.json"
    out.write_text(json.dumps(report, indent=1) + "\n")
    for q in QUERIES:
        print(f"{TARGET in before[q]} -> {TARGET in after[q]}  {q!r}")
    print(f"-> {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
