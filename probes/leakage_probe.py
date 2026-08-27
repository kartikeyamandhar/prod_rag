"""Incident 5 v2: tenant-isolation MECHANISM DEMO with the null model disclosed.

Audit B6 showed v1's headline ("47/47 queries leak with the filter off, 0/47
with it on") is arithmetic, not measurement: with ~50 tenants sharing one
corpus, a tenant-blind retriever is EXPECTED to return ~98% foreign results,
so observing that only verifies a WHERE clause. v2 keeps the demo (it is the
seeded incident) but computes the tenant-blind null from the actual tenant
distribution and prints it beside the observed value, so the reader sees the
observed filter-off arm IS the null. A real detection metric would need
adversarial cross-tenant content; that redesign is fenced (synthetic ticket
generation is banned by CLAUDE.md, and reshaping tenancy would invalidate
every other incident's corpus).

Run: uv run --env-file .env python -m probes.leakage_probe
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg

from probes.run_meta import run_meta
from retrieval.embedder import embed_query, get_query_embedder
from retrieval.search import hybrid_search

REPO_ROOT = Path(__file__).resolve().parent.parent


def run(conn: psycopg.Connection, tickets: list, embedder, enabled: bool) -> dict:
    queries_with_leak = 0
    foreign_total = 0
    ticket_results_total = 0
    with conn.cursor() as cur:
        cur.execute("SELECT number, tenant_id FROM tickets")
        tenant_of = dict(cur.fetchall())
    for _number, title, body, tenant_id in tickets:
        query = f"{title}\n{body[:500]}"
        qvec = embed_query(embedder, query)
        items = hybrid_search(conn, qvec, query, tenant_id, tenant_filter_enabled=enabled)
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
        "observed_foreign_fraction": round(foreign_total / ticket_results_total, 4)
        if ticket_results_total
        else None,
    }


def tenant_blind_null(conn: psycopg.Connection, tickets: list) -> dict:
    """Expected foreign fraction if retrieval ignored tenancy entirely: for a
    query from tenant t, P(foreign) = 1 - (searchable tickets of t / searchable
    tickets). The searchable pool excludes held-out tickets (never retrieved)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, count(*) FROM tickets"
            " WHERE NOT is_held_out AND embedding IS NOT NULL GROUP BY tenant_id"
        )
        pool = dict(cur.fetchall())
    total = sum(pool.values())
    per_query = [1 - pool.get(tenant_id, 0) / total for _n, _t, _b, tenant_id in tickets]
    return {
        "searchable_tickets": total,
        "predicted_foreign_fraction": round(sum(per_query) / len(per_query), 4),
        "meaning": "a tenant-blind retriever is expected to score this; matching it"
        " means the filter-off arm demonstrates a mechanism, not a detection metric",
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
            "framing": "mechanism demo; the filter-off arm reproduces the tenant-blind null",
            "n_held_out": len(tickets),
            "tenant_blind_null": tenant_blind_null(conn, tickets),
            "bug_seeded_filter_off": run(conn, tickets, embedder, enabled=False),
            "after_fix_filter_on": run(conn, tickets, embedder, enabled=True),
            "redesign_fenced": "adversarial cross-tenant content would need synthetic"
            " tickets (banned: circular eval) or retenanting (invalidates other incidents)",
            "run_meta": run_meta(conn),
        }
    out = REPO_ROOT / "artifacts" / "incidents" / "incident5_leakage.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in report if k != "run_meta"}, indent=1))


if __name__ == "__main__":
    main()
