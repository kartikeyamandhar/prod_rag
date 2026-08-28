"""Full held-out evaluation: every replayed incoming ticket through the live
pipeline, triage scored against real SIG labels, drafts judged against real
closing-PR content.

This produces the quality-tier headline table. Labeling is strict about what
each number is: triage accuracy is a PROXY metric against maintainer labels
(no judge involved); draft-quality scores are JUDGE-ONLY (rubric-anchored
Haiku judging Haiku drafts, real PR ground truth, cited spans in-prompt). The
30-ticket human agreement pass was waived by the project owner on 2026-08-28;
that waiver is recorded here and disclosed wherever these numbers appear.
Aborts if more than 5 drafts degrade (health gate, audit B1's lesson).

Run: uv run --env-file .env python -m probes.held_out_eval
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from pathlib import Path

import psycopg

from api.llm import BedrockLLM
from api.pipeline import TicketIn, handle_ticket
from probes.judge import judge_draft
from probes.run_meta import run_meta
from probes.stats import percentile
from retrieval.embedder import get_query_embedder

logging.basicConfig(level=logging.ERROR)
REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "artifacts" / "held_out_eval.json"
MAX_DEGRADED = 5


def main() -> None:
    pipeline_llm = BedrockLLM()
    judge_llm = BedrockLLM()
    embedder = get_query_embedder(os.environ["EMBED_MODEL_NAME"])
    pacing = float(os.environ.get("PROBE_PACING_S", "8"))

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT number, title, body, tenant_id, sigs, closing_pr_url,"
                " closing_pr_title, closing_pr_body FROM tickets WHERE is_held_out"
                " ORDER BY number"
            )
            cols = [c.name for c in cur.description]
            tickets = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        meta = run_meta(conn)

        rows: list[dict] = []
        for ticket_row in tickets:
            time.sleep(pacing)
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
            rows.append({"ticket": ticket_row, "response": response})
            print(
                f"#{ticket_row['number']} triage={response.triage.component}"
                f" hit={response.triage.component in ticket_row['sigs']}"
                f" route={response.route.route} degraded={response.degraded}"
            )

        degraded_count = sum(1 for r in rows if r["response"].degraded)
        print(f"\nHEALTH GATE: {degraded_count}/{len(rows)} degraded (max {MAX_DEGRADED})")
        if degraded_count > MAX_DEGRADED:
            raise SystemExit("health gate failed: fix the pipeline before judging")

        judged: list[dict] = []
        for row in rows:
            response = row["response"]
            if response.degraded:
                continue
            time.sleep(pacing)
            draft = response.draft.model_dump()
            cited_sources = {c["source"] for c in draft["citations"]}
            cited_context = [r for r in response.retrieval if r["key"] in cited_sources]
            scores = judge_draft(judge_llm, row["ticket"], draft, cited_context)
            judged.append({"ticket": row["ticket"]["number"], **scores})
            print(
                f"#{row['ticket']['number']} judge {scores['grounding']}/"
                f"{scores['cause_plausibility']}/{scores['actionability']}"
            )

    n = len(rows)
    triage_hits = sum(1 for r in rows if r["response"].triage.component in r["ticket"]["sigs"])
    routes = Counter(r["response"].route.route for r in rows)
    sufficiency = Counter(
        r["response"].draft.context_sufficiency
        for r in rows
        if r["response"].draft.context_sufficiency is not None
    )

    def dim(key: str) -> dict:
        values = [float(j[key]) for j in judged]
        return {
            "median": percentile(values, 50),
            "distribution": dict(sorted(Counter(int(v) for v in values).items())),
        }

    report = {
        "population": "all held-out tickets (replayed incoming)",
        "n": n,
        "triage_accuracy_vs_real_sig_labels": {
            "hits": triage_hits,
            "rate": round(triage_hits / n, 3),
            "labeling": "proxy metric vs maintainer labels; no judge involved",
        },
        "routes": dict(routes),
        "context_sufficiency_distribution": dict(sorted(sufficiency.items())),
        "degraded": degraded_count,
        "judged_n": len(judged),
        "judge_only_draft_quality": {
            "grounding": dim("grounding"),
            "cause_plausibility": dim("cause_plausibility"),
            "actionability": dim("actionability"),
            "labeling": "JUDGE-ONLY: rubric-anchored same-family judge with real"
            " closing-PR ground truth and cited spans; the 30-ticket human"
            " agreement pass was waived by the project owner (2026-08-28) and"
            " no kappa is claimed",
        },
        "judged_rows": judged,
        "pipeline_usage": pipeline_llm.meter.snapshot(),
        "judge_usage": judge_llm.meter.snapshot(),
        "run_meta": meta,
    }
    OUT.write_text(json.dumps(report, indent=1) + "\n")
    concise = {k: v for k, v in report.items() if k not in ("judged_rows", "run_meta")}
    print(json.dumps(concise, indent=1))
    print(f"-> {OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
