"""Fill the judge columns of the 30-ticket spot-check sheet with live judged runs (v2).

Two passes with a pre-registered HEALTH GATE between them (audit B1's lesson):
pass 1 runs the pipeline for all 30 sampled tickets and STOPS if more than 3
drafts degrade (read degrade_reasons, fix, rerun) so the judge never scores a
sheet contaminated by a broken pipeline. Pass 2 judges only non-degraded LLM
drafts; degraded rows are recorded in the sheet but excluded from the agreement
study. The human scores from the drafts JSONL, blind to the judge columns.

Run: uv run --env-file .env python -m probes.fill_spot_check
"""

from __future__ import annotations

import csv
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

logging.basicConfig(level=logging.ERROR)
REPO_ROOT = Path(__file__).resolve().parent.parent
SHEET = REPO_ROOT / "artifacts" / "spot_check_sampling_sheet.csv"
DRAFTS = REPO_ROOT / "artifacts" / "spot_check_drafts.jsonl"
MAX_DEGRADED = 3  # health gate: more than this and the run aborts before judging


def main() -> None:
    pipeline_llm = BedrockLLM()
    judge_llm = BedrockLLM()
    embedder = get_query_embedder(os.environ["EMBED_MODEL_NAME"])
    pacing = float(os.environ.get("PROBE_PACING_S", "8"))

    with SHEET.open() as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit("spot-check sheet is empty; run make_spot_check_sheet first")

    # Pass 1: pipeline for all sampled tickets.
    runs: list[dict] = []
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for row in rows:
            time.sleep(pacing)
            number = int(row["ticket_number"])
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT number, title, body, tenant_id, sigs, closing_pr_url,"
                    " closing_pr_title, closing_pr_body FROM tickets WHERE number = %s",
                    (number,),
                )
                fetched = cur.fetchone()
                if fetched is None:
                    raise SystemExit(f"ticket {number} in sheet but not in DB; reseed first")
                cols = [c.name for c in cur.description]
                ticket_row = dict(zip(cols, fetched, strict=True))
            response = handle_ticket(
                conn,
                embedder,
                TicketIn(
                    title=ticket_row["title"],
                    body=ticket_row["body"][:4000],
                    tenant_id=ticket_row["tenant_id"],
                ),
                llm=pipeline_llm,
            )
            runs.append({"row": row, "ticket_row": ticket_row, "response": response})
            print(
                f"#{number} route={response.route.route} degraded={response.degraded}"
                f" reasons={response.degrade_reasons or 'none'}"
            )

        degraded_count = sum(1 for run in runs if run["response"].degraded)
        print(f"\nHEALTH GATE: {degraded_count}/{len(runs)} degraded (max {MAX_DEGRADED})")
        if degraded_count > MAX_DEGRADED:
            for run in runs:
                if run["response"].degraded:
                    print(f"  #{run['row']['ticket_number']}: {run['response'].degrade_reasons}")
            raise SystemExit("health gate failed: fix the pipeline before judging")

        # Pass 2: judge only non-degraded LLM drafts.
        with DRAFTS.open("w", encoding="utf-8") as drafts_out:
            for run in runs:
                row, ticket_row, response = run["row"], run["ticket_row"], run["response"]
                draft = response.draft.model_dump()
                row["degraded"] = str(response.degraded)
                row["draft_source"] = "extractive" if response.degraded else "llm"
                row["route"] = response.route.route
                row["degrade_reasons"] = "; ".join(response.degrade_reasons)
                scores = None
                if not response.degraded:
                    time.sleep(pacing)
                    cited_sources = {c["source"] for c in draft["citations"]}
                    cited_context = [r for r in response.retrieval if r["key"] in cited_sources]
                    scores = judge_draft(judge_llm, ticket_row, draft, cited_context)
                    row["judge_grounding"] = scores["grounding"]
                    row["judge_cause_plausibility"] = scores["cause_plausibility"]
                    row["judge_actionability"] = scores["actionability"]
                else:
                    row["judge_grounding"] = ""
                    row["judge_cause_plausibility"] = ""
                    row["judge_actionability"] = ""
                drafts_out.write(
                    json.dumps(
                        {
                            "ticket": int(row["ticket_number"]),
                            "url": ticket_row["closing_pr_url"],
                            "route": response.route.route,
                            "degraded": response.degraded,
                            "degrade_reasons": response.degrade_reasons,
                            "draft": draft,
                            "judge": scores,
                        }
                    )
                    + "\n"
                )

    with SHEET.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nPIPELINE: {pipeline_llm.meter.snapshot()}")
    print(f"JUDGE:    {judge_llm.meter.snapshot()}")
    judged = sum(1 for run in runs if not run["response"].degraded)
    print(f"Judged {judged}/{len(runs)} (agreement study population); sheet + drafts updated")


if __name__ == "__main__":
    main()
