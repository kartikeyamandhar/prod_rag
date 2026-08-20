"""W2 replay harness: held-out tickets replayed as incoming through the live API.

Retrieval-tier metrics only, against ground truth the corpus already carries:
SIG labels (triage truth) and corpus provenance (did retrieval surface a same-SIG
resolved ticket, and a docs page from the SIG's home directory). Stub-tier table;
CLAUDE.md forbids publishing stub numbers, so the artifact is labeled accordingly.

Run (API must be up): uv run --env-file .env python -m probes.replay_harness
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
import psycopg

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
API = os.environ.get("API_URL", "http://127.0.0.1:8080")
N_TICKETS = 15
SIG_HOME_DIR = {
    "sig/network": "services-networking",
    "sig/scheduling": "scheduling-eviction",
    "sig/storage": "storage",
}


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT number, title, body, tenant_id, sigs FROM tickets"
            " WHERE is_held_out ORDER BY number DESC LIMIT %s",
            (N_TICKETS,),
        )
        tickets = cur.fetchall()
        cur.execute("SELECT number, sigs FROM tickets")
        ticket_sigs = dict(cur.fetchall())

    rows = []
    with httpx.Client(timeout=30.0) as client:
        for number, title, body, tenant_id, sigs in tickets:
            response = client.post(
                f"{API}/tickets",
                json={"title": title, "body": body[:4000], "tenant_id": tenant_id},
            )
            response.raise_for_status()
            data = response.json()

            triage_hit = data["triage"]["component"] in sigs
            retrieved = data["retrieval"]
            docs_hits = [
                r
                for r in retrieved
                if r["corpus"] == "docs"
                and any(SIG_HOME_DIR[s] in r["url"] for s in sigs if s in SIG_HOME_DIR)
            ]
            tick_hits = [
                r
                for r in retrieved
                if r["corpus"] == "tickets"
                and any(s in ticket_sigs.get(int(r["key"].split(":")[1]), []) for s in sigs)
            ]
            rows.append(
                {
                    "number": number,
                    "sigs": "+".join(s.removeprefix("sig/") for s in sigs),
                    "triage": data["triage"]["component"].removeprefix("sig/"),
                    "triage_hit": triage_hit,
                    "docs_domain_hits": len(docs_hits),
                    "same_sig_ticket_hits": len(tick_hits),
                    "route": data["route"]["route"],
                    "confidence": data["route"]["confidence"],
                    "retrieval_ms": data["timings_ms"]["retrieval"],
                }
            )

    header = (
        "| ticket | truth SIGs | stub triage | triage hit | docs-domain in top8 |"
        " same-SIG tickets in top8 | route | conf | retr ms |"
    )
    sep = "|" + "---|" * 9
    lines = [
        "# W2 replay table: 15 held-out tickets through the STUBBED pipeline",
        "",
        "Stub-tier numbers. Per CLAUDE.md these never appear in published posts;",
        "they exist to prove the harness end to end before Bedrock wiring.",
        "",
        header,
        sep,
    ]
    for row in rows:
        lines.append(
            f"| #{row['number']} | {row['sigs']} | {row['triage']} |"
            f" {'Y' if row['triage_hit'] else 'N'} | {row['docs_domain_hits']} |"
            f" {row['same_sig_ticket_hits']} | {row['route']} |"
            f" {row['confidence']:.2f} | {row['retrieval_ms']:.0f} |"
        )
    triage_rate = sum(r["triage_hit"] for r in rows) / len(rows)
    docs_rate = sum(r["docs_domain_hits"] > 0 for r in rows) / len(rows)
    tick_rate = sum(r["same_sig_ticket_hits"] > 0 for r in rows) / len(rows)
    routes = {
        route: sum(1 for r in rows if r["route"] == route)
        for route in ("auto_attach", "escalate", "request_info")
    }
    summary = (
        f"\nSummary: triage-hit {triage_rate:.0%}; >=1 docs-domain page in top8 {docs_rate:.0%};"
        f" >=1 same-SIG ticket in top8 {tick_rate:.0%}; routes {routes}"
    )
    lines.append(summary)

    table = "\n".join(lines)
    print(table)
    out = REPO_ROOT / "artifacts" / "replay_w2_table.md"
    out.write_text(table + "\n", encoding="utf-8")
    print(f"\nARCHIVED: {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
