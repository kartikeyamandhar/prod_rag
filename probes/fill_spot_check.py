"""Fill the judge columns of the 30-ticket spot-check sheet with live judged runs.

For each sampled ticket: full LLM pipeline (triage + draft), then the rubric judge.
Judge scores land in the CSV; the drafts and rationales land in a JSONL so the
human reviewer can score the same drafts independently. Human columns stay empty:
that part is the whole point.

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


def main() -> None:
    pipeline_llm = BedrockLLM()
    judge_llm = BedrockLLM()
    embedder = get_query_embedder(os.environ["EMBED_MODEL_NAME"])

    with SHEET.open() as fh:
        rows = list(csv.DictReader(fh))

    with (
        psycopg.connect(os.environ["DATABASE_URL"]) as conn,
        DRAFTS.open("w", encoding="utf-8") as drafts_out,
    ):
        for row in rows:
            time.sleep(float(os.environ.get("PROBE_PACING_S", "8")))
            number = int(row["ticket_number"])
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT number, title, body, tenant_id, sigs, closing_pr_url"
                    " FROM tickets WHERE number = %s",
                    (number,),
                )
                cols = [c.name for c in cur.description]
                ticket_row = dict(zip(cols, cur.fetchone(), strict=True))
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
            draft = response.draft.model_dump()
            scores = judge_draft(judge_llm, ticket_row, draft)
            row["judge_grounding"] = scores["grounding"]
            row["judge_cause_plausibility"] = scores["cause_plausibility"]
            row["judge_actionability"] = scores["actionability"]
            drafts_out.write(
                json.dumps(
                    {
                        "ticket": number,
                        "url": ticket_row["closing_pr_url"],
                        "route": response.route.route,
                        "degraded": response.degraded,
                        "draft": draft,
                        "judge": scores,
                    }
                )
                + "\n"
            )
            print(
                f"#{number} route={response.route.route}"
                f" judge={scores['grounding']}/{scores['cause_plausibility']}"
                f"/{scores['actionability']}"
            )

    with SHEET.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nPIPELINE: {pipeline_llm.meter.snapshot()}")
    print(f"JUDGE:    {judge_llm.meter.snapshot()}")
    print(f"Sheet updated: {SHEET.name}; drafts for human review: {DRAFTS.name}")


if __name__ == "__main__":
    main()
