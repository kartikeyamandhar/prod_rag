"""Bootstrap loader: ticket parquet artifact into Postgres.

Truncates and reloads wholesale, like the docs loader; the ticket corpus is a fixed
snapshot and never receives incremental updates (CLAUDE.md invariant).

Run: uv run --env-file .env python -m tickets.load_tickets artifacts/tickets_<date>.parquet
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
from pgvector.psycopg import register_vector

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent


def main(parquet_path: Path) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set; run via: uv run --env-file .env ...")

    table = pq.read_table(parquet_path)
    bad = [
        row["number"]
        for row in table.to_pylist()
        if row["embedding"] is not None and len(row["embedding"]) != 384
    ]
    if bad:
        raise SystemExit(f"non-384 embeddings for tickets {bad[:5]} (total {len(bad)})")
    schema_sql = (REPO_ROOT / "tickets" / "schema.sql").read_text(encoding="utf-8")

    with psycopg.connect(database_url) as conn:
        conn.execute(schema_sql)
        conn.commit()
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute("TRUNCATE tickets")
            logger.info("truncated tickets for bootstrap load")
            cur.executemany(
                "INSERT INTO tickets (number, title, body, labels, sigs, created_at,"
                " closed_at, url, closing_pr_number, closing_pr_url, tenant_id,"
                " is_held_out, has_images, snapshot_date, embed_text, embedding)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        row["number"],
                        row["title"],
                        row["body"],
                        row["labels"],
                        row["sigs"],
                        row["created_at"],
                        row["closed_at"],
                        row["url"],
                        row["closing_pr_number"],
                        row["closing_pr_url"],
                        row["tenant_id"],
                        row["is_held_out"],
                        row["has_images"],
                        row["snapshot_date"],
                        row["embed_text"],
                        row["embedding"],
                    )
                    for row in table.to_pylist()
                ],
            )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT count(*), count(embedding) FROM tickets")
            fetched = cur.fetchone()
    assert fetched is not None
    db_total, db_embedded = fetched
    parquet_embedded = len(table.column("embedding").drop_null())
    print(f"DB tickets={db_total} embedded={db_embedded}")
    print(f"PARQUET tickets={table.num_rows} embedded={parquet_embedded}")
    match = db_total == table.num_rows and db_embedded == parquet_embedded
    print(f"COUNTS MATCH: {match}")
    if not match:
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m tickets.load_tickets <tickets_*.parquet>")
    main(Path(sys.argv[1]))
