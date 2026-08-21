"""Incident 5 probe: tenant leakage incidence across ALL held-out tickets.

Runs every held-out ticket as a query twice: with the tenant filter intact (fix)
and with the seeded bug (filter dropped). Counts foreign-tenant ticket results.

Run: uv run --env-file .env python -m probes.leakage_probe
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg

from retrieval.embedder import embed_query, get_query_embedder
from retrieval.search import hybrid_search

REPO_ROOT = Path(__file__).resolve().parent.parent


def run(conn: psycopg.Connection, tickets: list, embedder, enabled: bool) -> dict:
    queries_with_leak = 0
    foreign_total = 0
    ticket_results_total = 0
    tenant_of = {}
    with conn.cursor() as cur:
        cur.execute("SELECT number, tenant_id FROM tickets")
        tenant_of = dict(cur.fetchall())
    for number, title, body, tenant_id in tickets:
        qvec = embed_query(embedder, title)
        items = hybrid_search(conn, qvec, title, tenant_id, tenant_filter_enabled=enabled)
        foreign = [
            i
            for i in items
            if i.corpus == "tickets" and tenant_of[int(i.key.split(":")[1])] != tenant_id
        ]
        ticket_results_total += sum(1 for i in items if i.corpus == "tickets")
        foreign_total += len(foreign)
        queries_with_leak += bool(foreign)
    n = len(tickets)
    return {
        "tenant_filter_enabled": enabled,
        "queries": n,
        "queries_with_foreign_results": queries_with_leak,
        "leak_incidence": round(queries_with_leak / n, 3),
        "foreign_results_total": foreign_total,
        "ticket_results_total": ticket_results_total,
    }


def main() -> None:
    embedder = get_query_embedder(os.environ["EMBED_MODEL_NAME"])
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT number, title, body, tenant_id FROM tickets WHERE is_held_out"
                " ORDER BY number"
            )
            tickets = cur.fetchall()
        report = {
            "incident": 5,
            "n_held_out": len(tickets),
            "before_seeded_bug": run(conn, tickets, embedder, enabled=False),
            "after_fix": run(conn, tickets, embedder, enabled=True),
        }
    out = REPO_ROOT / "artifacts" / "incidents" / "incident5_leakage.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
