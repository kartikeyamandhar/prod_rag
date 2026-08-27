"""Metered Bedrock smoke: 5 held-out tickets through the LIVE pipeline plus judge.

Converts cost estimates into measured numbers (CLAUDE.md W4). Pipeline calls and
judge calls run on separate meters so their spend is attributable. Results are
archived as JSON in artifacts/.

Run: uv run --env-file .env python -m probes.metered_smoke
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from pathlib import Path

import psycopg

from api.llm import BedrockLLM
from api.pipeline import TicketIn, handle_ticket
from probes.judge import judge_draft
from retrieval.embedder import get_query_embedder

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parent.parent
N = 5


def main() -> None:
    pipeline_llm = BedrockLLM()
    judge_llm = BedrockLLM()
    embedder = get_query_embedder(os.environ["EMBED_MODEL_NAME"])
    results = []

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT number, title, body, tenant_id, sigs, closing_pr_url,"
                " closing_pr_title, closing_pr_body"
                " FROM tickets WHERE is_held_out ORDER BY number DESC LIMIT %s",
                (N,),
            )
            rows = [
                dict(zip([c.name for c in cur.description], row, strict=True))
                for row in cur.fetchall()
            ]

        for row in rows:
            time.sleep(float(os.environ.get("PROBE_PACING_S", "8")))
            ticket = TicketIn(
                title=row["title"], body=row["body"][:4000], tenant_id=row["tenant_id"]
            )
            response = handle_ticket(conn, embedder, ticket, llm=pipeline_llm)
            draft = response.draft.model_dump()
            cited_sources = {c["source"] for c in draft["citations"]}
            cited_context = [r for r in response.retrieval if r["key"] in cited_sources]
            scores = judge_draft(judge_llm, row, draft, cited_context)
            results.append(
                {
                    "ticket": row["number"],
                    "truth_sigs": row["sigs"],
                    "llm_triage": response.triage.component,
                    "triage_hit": response.triage.component in row["sigs"],
                    "severity": response.triage.severity,
                    "route": response.route.route,
                    "confidence": response.route.confidence,
                    "degraded": response.degraded,
                    "degrade_reasons": response.degrade_reasons,
                    "n_citations": len(response.draft.citations),
                    "judge": scores,
                }
            )
            print(
                f"#{row['number']} triage={response.triage.component}"
                f" hit={response.triage.component in row['sigs']}"
                f" route={response.route.route}({response.route.confidence:.2f})"
                f" degrade={response.degrade_reasons or 'none'}"
                f" judge={scores['grounding']}/{scores['cause_plausibility']}"
                f"/{scores['actionability']}"
            )

    report = {
        "date": dt.date.today().isoformat(),
        "model_id": pipeline_llm.model_id,
        "n_tickets": len(results),
        "results": results,
        "pipeline_usage": pipeline_llm.meter.snapshot(),
        "judge_usage": judge_llm.meter.snapshot(),
    }
    total = round(
        report["pipeline_usage"]["usd_at_list_rates"] + report["judge_usage"]["usd_at_list_rates"],
        4,
    )
    report["total_usd_at_list_rates"] = total
    out = REPO_ROOT / "artifacts" / f"metered_smoke_{report['date']}.json"
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"\nPIPELINE USAGE: {report['pipeline_usage']}")
    print(f"JUDGE USAGE:    {report['judge_usage']}")
    print(f"TOTAL MEASURED COST (first-party list rates): ${total}")
    print(f"ARCHIVED: {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
