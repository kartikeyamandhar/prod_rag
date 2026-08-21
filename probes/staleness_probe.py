"""Incident 2 probe: stale-serving incidence for pages changed upstream.

For every page the pending upstream commits modify, query retrieval with the page
title and check whether the top-5 docs results serve chunks from a page that is
stale (DB corpus_sha still at a commit older than the upstream change).

Run (box or laptop): uv run --env-file .env python -m probes.staleness_probe --tag before
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg
from git import Repo

from ingest.clone_docs import CORPUS_DIR, SPARSE_PATH
from ingest.manifest import load_manifest
from retrieval.embedder import embed_query, get_query_embedder
from updater.replayer import parse_name_status

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()

    manifest = load_manifest(REPO_ROOT / "corpus_manifest.toml")
    pin = manifest.docs_corpus.start_sha
    repo = Repo(CORPUS_DIR)
    diff_text = repo.git.diff("--name-status", "-M", pin, "origin/main", "--", SPARSE_PATH)
    changed = [c.path for c in parse_name_status(diff_text) if c.change_type == "modified"]

    embedder = get_query_embedder(os.environ["EMBED_MODEL_NAME"])
    rows = []
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT path, title, corpus_sha FROM pages WHERE path = ANY(%s)", (changed,))
        page_info = {path: (title, sha) for path, title, sha in cur.fetchall()}
        # A changed page is stale while its DB corpus_sha predates the upstream change,
        # i.e. it is still at the pin (or an already-applied commit that is not the
        # one touching it). Simplification that holds here: stale == corpus_sha == pin.
        stale_pages = {p for p, (_, sha) in page_info.items() if sha == pin}

        for path in changed:
            if path not in page_info:
                continue
            title, _ = page_info[path]
            qvec = embed_query(embedder, title)
            cur.execute(
                "SELECT p.path FROM chunks c JOIN pages p ON p.id = c.page_id"
                " ORDER BY c.embedding <=> %s::vector LIMIT 5",
                (qvec,),
            )
            top_paths = [r[0] for r in cur.fetchall()]
            served_stale = [p for p in top_paths if p in stale_pages]
            rows.append({"query": title, "page": path, "stale_chunks_in_top5": len(served_stale)})

    n = len(rows)
    incidence = sum(1 for r in rows if r["stale_chunks_in_top5"] > 0)
    report = {
        "incident": 2,
        "tag": args.tag,
        "changed_pages_probed": n,
        "stale_pages_in_db": len(stale_pages),
        "queries_serving_stale_content": incidence,
        "stale_serving_incidence": round(incidence / n, 3) if n else None,
        "rows": rows,
    }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
