"""Bootstrap loader: docs parquet artifacts into Postgres.

Destructive by design at bootstrap: truncates pages and chunks, then loads the
artifact wholesale. Incremental updates are the Phase 6 replayer's job, never this
script's. DATABASE_URL comes from the environment (run via uv run --env-file .env).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
from pgvector.psycopg import register_vector

from ingest.manifest import load_manifest

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = REPO_ROOT / "artifacts"


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set; run via: uv run --env-file .env ...")

    manifest = load_manifest(REPO_ROOT / "corpus_manifest.toml")
    sha7 = manifest.docs_corpus.start_sha[:7]
    pages_table = pq.read_table(ARTIFACTS / f"docs_pages_{sha7}.parquet")
    chunks_table = pq.read_table(ARTIFACTS / f"docs_chunks_{sha7}.parquet")

    schema_sql = (REPO_ROOT / "ingest" / "schema.sql").read_text(encoding="utf-8")

    with psycopg.connect(database_url) as conn:
        conn.execute(schema_sql)
        conn.commit()
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute("TRUNCATE pages CASCADE")
            logger.info("truncated pages and chunks for bootstrap load")

            page_ids: dict[str, int] = {}
            for row in pages_table.to_pylist():
                cur.execute(
                    "INSERT INTO pages (path, title, corpus_sha, has_images, n_chunks)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (
                        row["path"],
                        row["title"],
                        row["start_sha"],
                        row["has_images"],
                        row["n_chunks"],
                    ),
                )
                fetched = cur.fetchone()
                assert fetched is not None
                page_ids[row["path"]] = fetched[0]

            chunk_rows = chunks_table.to_pylist()
            cur.executemany(
                "INSERT INTO chunks (page_id, chunk_index, section, text, n_chars, embedding)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (
                        page_ids[row["page_path"]],
                        row["chunk_index"],
                        row["section"],
                        row["text"],
                        row["n_chars"],
                        row["embedding"],
                    )
                    for row in chunk_rows
                ],
            )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pages")
            db_pages = cur.fetchone()
            cur.execute("SELECT count(*) FROM chunks")
            db_chunks = cur.fetchone()
    assert db_pages is not None and db_chunks is not None
    print(f"DB pages={db_pages[0]} chunks={db_chunks[0]}")
    print(f"PARQUET pages={pages_table.num_rows} chunks={chunks_table.num_rows}")
    match = db_pages[0] == pages_table.num_rows and db_chunks[0] == chunks_table.num_rows
    print(f"COUNTS MATCH: {match}")
    if not match:
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    main()
