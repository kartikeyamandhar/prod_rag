"""Phase 2 gate script: smoke retrieval, tenant-filter proof, latency, concurrency.

Run: uv run --env-file .env python -m retrieval.smoke
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import statistics
import time
from pathlib import Path

import psycopg

from ingest.manifest import load_manifest
from retrieval.embedder import embed_query, get_query_embedder
from retrieval.search import RetrievedItem, hybrid_search

logger = logging.getLogger(__name__)


def _show(items: list[RetrievedItem]) -> None:
    for i, item in enumerate(items, 1):
        ranks = ",".join(f"{k}#{v}" for k, v in sorted(item.ranks.items()))
        print(f"  {i}. [{item.corpus}] {item.title[:60]} | {ranks} | rrf={item.score:.4f}")


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    manifest = load_manifest(Path(__file__).resolve().parent.parent / "corpus_manifest.toml")
    model = get_query_embedder(manifest.docs_corpus.embed_model)

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT number, title, tenant_id FROM tickets WHERE is_held_out"
                " ORDER BY number DESC LIMIT 3"
            )
            samples = cur.fetchall()

        print("=== GATE 1: smoke retrieval on 3 held-out tickets (incoming queries) ===")
        for number, title, tenant_id in samples:
            print(f"\nQUERY ticket #{number} (tenant {tenant_id}): {title[:70]}")
            qvec = embed_query(model, title)
            _show(hybrid_search(conn, qvec, title, tenant_id))

        print("\n=== GATE 2: tenant filter binds (same query, filter on vs off) ===")
        number, title, tenant_id = samples[0]
        qvec = embed_query(model, title)
        on = hybrid_search(conn, qvec, title, tenant_id, tenant_filter_enabled=True)
        off = hybrid_search(conn, qvec, title, tenant_id, tenant_filter_enabled=False)
        on_tickets = [i.key for i in on if i.corpus == "tickets"]
        off_tickets = [i.key for i in off if i.corpus == "tickets"]
        print(f"filter ON  tenant={tenant_id}: ticket results {on_tickets}")
        print(f"filter OFF tenant={tenant_id}: ticket results {off_tickets}")
        with conn.cursor() as cur:
            leaked = []
            for key in off_tickets:
                cur.execute(
                    "SELECT tenant_id FROM tickets WHERE number = %s", (int(key.split(":")[1]),)
                )
                row = cur.fetchone()
                assert row is not None
                if row[0] != tenant_id:
                    leaked.append((key, row[0]))
            on_foreign = []
            for key in on_tickets:
                cur.execute(
                    "SELECT tenant_id FROM tickets WHERE number = %s", (int(key.split(":")[1]),)
                )
                row = cur.fetchone()
                assert row is not None
                if row[0] != tenant_id:
                    on_foreign.append(key)
        print(f"foreign-tenant results with filter ON: {on_foreign} (must be [])")
        print(f"foreign-tenant results with filter OFF: {leaked} (leakage is observable)")
        if on_foreign:
            raise SystemExit("TENANT FILTER DOES NOT BIND")

        print("\n=== GATE 3: latency, 20 sequential calls (call 1 includes model+plan warmup) ===")
        latencies = []
        for _ in range(20):
            t0 = time.perf_counter()
            qvec = embed_query(model, title)
            hybrid_search(conn, qvec, title, tenant_id)
            latencies.append((time.perf_counter() - t0) * 1000)
        print("  ms per call:", " ".join(f"{ms:.0f}" for ms in latencies))
        warm = latencies[1:]
        print(
            f"  warm: mean={statistics.mean(warm):.1f}ms p50={statistics.median(warm):.1f}ms"
            f" max={max(warm):.1f}ms"
        )

    print("\n=== GATE 4 evidence: concurrency (8 threads, own connection each) ===")

    def worker(worker_id: int) -> list[str]:
        with psycopg.connect(database_url) as wconn:
            wvec = embed_query(model, title)
            return [item.key for item in hybrid_search(wconn, wvec, title, tenant_id)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(worker, range(8)))
    identical = all(outcome == outcomes[0] for outcome in outcomes)
    print(f"  8 concurrent retrievals, all results identical to each other: {identical}")
    if not identical:
        raise SystemExit("CONCURRENT RETRIEVAL NONDETERMINISM")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
