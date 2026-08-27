"""R2 A/B: FTS query mode (legacy AND vs OR-ified), $0. The bge-prefix arm ran once
(2026-08-27) and was dropped by its pre-registered rule; see embedder docstring.

Retrieval-tier only, over the 15 held-out W2 queries on one DB state. Emits the
observables that decide two pre-registered gates:
- R2: FTS non-empty rates, items[0] corpus distribution, multi-list membership,
  quality metrics non-decreasing.
- R1.9: adopt the bge query prefix iff same-SIG@8 and docs-domain@8 are
  non-decreasing AND MRR(first same-SIG ticket) improves by >= 0.02.

Run: uv run --env-file .env python -m probes.r2_fts_ab
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

import psycopg

from probes.replay_harness import SIG_HOME_DIR
from retrieval.embedder import embed_query, get_query_embedder
from retrieval.search import hybrid_search

REPO_ROOT = Path(__file__).resolve().parent.parent
N = 15


def measure(conn, embedder, tickets, ticket_sigs, fts_mode: str) -> dict:
    same_sig = docs_domain = 0
    fts_nonempty = {"docs_fts": 0, "tickets_fts": 0}
    top1_docs = 0
    multi_list_items = 0
    total_items = 0
    mrr_values: list[float] = []
    latencies: list[float] = []
    for _number, title, body, tenant_id, sigs in tickets:
        query = f"{title}\n{body[:500]}"
        t0 = time.perf_counter()
        qvec = embed_query(embedder, query)
        items = hybrid_search(conn, qvec, query, tenant_id, fts_mode=fts_mode)
        latencies.append((time.perf_counter() - t0) * 1000)

        lists_seen = {name for item in items for name in item.ranks}
        fts_nonempty["docs_fts"] += "docs_fts" in lists_seen
        fts_nonempty["tickets_fts"] += "tickets_fts" in lists_seen
        top1_docs += bool(items) and items[0].corpus == "docs"
        multi_list_items += sum(1 for item in items if len(item.ranks) >= 2)
        total_items += len(items)

        first_same_sig_rank = None
        for rank, item in enumerate(items, 1):
            if item.corpus == "tickets" and set(
                ticket_sigs.get(int(item.key.split(":")[1]), [])
            ) & set(sigs):
                if first_same_sig_rank is None:
                    first_same_sig_rank = rank
        if first_same_sig_rank:
            same_sig += 1
            mrr_values.append(1.0 / first_same_sig_rank)
        else:
            mrr_values.append(0.0)
        if any(
            item.corpus == "docs"
            and any(SIG_HOME_DIR[s] in (item.url or "") for s in sigs if s in SIG_HOME_DIR)
            for item in items
        ):
            docs_domain += 1

    return {
        "fts_mode": fts_mode,
        "same_sig_at8": same_sig,
        "docs_domain_at8": docs_domain,
        "mrr_first_same_sig": round(statistics.mean(mrr_values), 4),
        "docs_fts_nonempty": fts_nonempty["docs_fts"],
        "tickets_fts_nonempty": fts_nonempty["tickets_fts"],
        "top1_is_docs": top1_docs,
        "multi_list_item_fraction": round(multi_list_items / total_items, 3) if total_items else 0,
        "latency_p50_ms": round(statistics.median(latencies), 1),
    }


def main() -> None:
    embedder = get_query_embedder(os.environ["EMBED_MODEL_NAME"])
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT number, title, body, tenant_id, sigs FROM tickets"
                " WHERE is_held_out ORDER BY number DESC LIMIT %s",
                (N,),
            )
            tickets = cur.fetchall()
            cur.execute("SELECT number, sigs FROM tickets")
            ticket_sigs = dict(cur.fetchall())

        arms = [
            measure(conn, embedder, tickets, ticket_sigs, fts_mode=mode)
            for mode in ("legacy", "lexical")
        ]
    report = {"n_queries": N, "arms": arms}
    out = REPO_ROOT / "artifacts" / "r2_fts_ab.json"
    out.write_text(json.dumps(report, indent=1) + "\n")
    for arm in arms:
        print(arm)
    print(f"ARCHIVED: {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
