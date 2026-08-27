"""Incident 2 probe v2: content-hash staleness, measured through the real path.

v1 defined stale as corpus_sha == pin, which after a full replay is empty by
definition (the metric restated the tick counter, audit B9), and probed with
raw vector top-5 using each page's own title as the query (near-tautological).
v2: a DB page is STALE iff chunking the origin/main version of its file yields
different text than the DB holds (content actually changed, not just SHA
bookkeeping); queries are held-out ticket titles+bodies through hybrid_search,
the pipeline's actual retrieval path. Denominators disclosed: pages modified
upstream, of those content-changed vs chunk-identical, plus added/deleted
pages this metric cannot see.

Run: uv run --env-file .env python -m probes.staleness_probe --tag before
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import psycopg
from git import Repo

from ingest.chunker import chunk_page
from ingest.clone_docs import CORPUS_DIR, SPARSE_PATH
from ingest.manifest import load_manifest
from probes.run_meta import run_meta
from retrieval.embedder import embed_query, get_query_embedder
from retrieval.search import hybrid_search

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "artifacts" / "incidents"


def _chunk_hash(path: str, raw_markdown: str) -> str:
    page = chunk_page(path, raw_markdown)
    joined = "\n".join(chunk.text for chunk in page.chunks)
    return hashlib.md5(joined.encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--no-fetch", action="store_true", help="skip git fetch (offline rerun)")
    args = parser.parse_args()

    manifest = load_manifest(REPO_ROOT / "corpus_manifest.toml")
    pin = manifest.docs_corpus.start_sha
    repo = Repo(CORPUS_DIR)
    if not args.no_fetch:
        repo.git.fetch("origin", "main")

    from updater.replayer import parse_name_status

    diff_text = repo.git.diff("--name-status", "-M", pin, "origin/main", "--", SPARSE_PATH)
    changes = parse_name_status(diff_text)
    modified = [c.path for c in changes if c.change_type == "modified"]
    added = [c.path for c in changes if c.change_type == "added"]
    deleted = [c.path for c in changes if c.change_type == "deleted"]
    renamed = [c.path for c in changes if c.change_type == "renamed"]

    embedder = get_query_embedder(os.environ["EMBED_MODEL_NAME"])
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        # Stale = the upstream edit actually changes what our chunker would store.
        stale_pages: set[str] = set()
        chunk_identical: list[str] = []
        missing_from_db: list[str] = []
        with conn.cursor() as cur:
            for path in modified:
                cur.execute(
                    "SELECT md5(string_agg(c.text, E'\\n' ORDER BY c.chunk_index))"
                    " FROM chunks c JOIN pages p ON p.id = c.page_id WHERE p.path = %s",
                    (path,),
                )
                fetched = cur.fetchone()
                db_hash = fetched[0] if fetched else None
                if db_hash is None:
                    missing_from_db.append(path)
                    continue
                upstream_raw = repo.git.show(f"origin/main:{path}")
                if _chunk_hash(path, upstream_raw) != db_hash:
                    stale_pages.add(path)
                else:
                    chunk_identical.append(path)

            cur.execute(
                "SELECT number, title, body, tenant_id FROM tickets"
                " WHERE is_held_out ORDER BY number"
            )
            tickets = cur.fetchall()

        rows = []
        for number, title, body, tenant_id in tickets:
            query = f"{title}\n{body[:500]}"
            qvec = embed_query(embedder, query)
            items = hybrid_search(conn, qvec, query, tenant_id)
            stale_served = [
                item.key for item in items if item.corpus == "docs" and item.url in stale_pages
            ]
            rows.append(
                {"ticket": number, "stale_docs_in_top8": len(stale_served), "keys": stale_served}
            )

        meta = run_meta(conn)

    n = len(rows)
    incidence = sum(1 for r in rows if r["stale_docs_in_top8"] > 0)
    report = {
        "incident": 2,
        "tag": args.tag,
        "staleness_definition": "chunked origin/main text differs from DB chunk text",
        "upstream_pages": {
            "modified": len(modified),
            "of_modified_content_changed": len(stale_pages),
            "of_modified_chunk_identical": len(chunk_identical),
            "of_modified_missing_from_db": len(missing_from_db),
            "added_not_measurable": len(added),
            "deleted_not_measurable": len(deleted),
            "renamed_not_measurable": len(renamed),
        },
        "stale_pages": sorted(stale_pages),
        "queries": n,
        "queries_serving_stale_docs": incidence,
        "stale_serving_incidence": round(incidence / n, 3) if n else None,
        "rows": rows,
        "run_meta": meta,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"incident2_{args.tag}.json"
    out.write_text(json.dumps(report, indent=1) + "\n")
    summary = {k: v for k, v in report.items() if k not in ("rows", "stale_pages")}
    print(json.dumps(summary, indent=1))
    print(f"-> {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
