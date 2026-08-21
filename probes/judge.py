"""Rubric-anchored judge: scores a drafted first response against ground truth.

The rubric is pinned in probes/rubric.md and injected verbatim; the judge never
free-styles its criteria. Judged numbers are unpublishable until the 30-ticket
human spot-check of judge agreement is done (CLAUDE.md invariant).
"""

from __future__ import annotations

import logging
from pathlib import Path

from api.llm import BedrockLLM, extract_json

logger = logging.getLogger(__name__)

RUBRIC = (Path(__file__).resolve().parent / "rubric.md").read_text(encoding="utf-8")

SYSTEM = (
    "You are a strict evaluation judge for support-ticket first responses."
    " Score EXACTLY per the rubric below. Respond with strict JSON only.\n\n" + RUBRIC
)
PROMPT = """Score this drafted first response.

Ticket title: {title}
Ticket body (excerpt):
{body}

Ground truth: the ticket's real SIG labels are {sigs}; it was resolved by {resolution}.

Draft under evaluation:
probable_cause: {probable_cause}
suggested_fix: {suggested_fix}
citations: {citations}
clarifying_questions: {questions}

Return JSON: {{"grounding": 1-5, "cause_plausibility": 1-5, "actionability": 1-5,
"rationale": "2-4 sentences"}}
"""


def judge_draft(llm: BedrockLLM, ticket_row: dict, draft: dict) -> dict:
    prompt = PROMPT.format(
        title=ticket_row["title"],
        body=ticket_row["body"][:1500],
        sigs=ticket_row["sigs"],
        resolution=ticket_row.get("closing_pr_url") or "an unlinked resolution",
        probable_cause=draft["probable_cause"],
        suggested_fix=draft["suggested_fix"],
        citations=[(c["source"], c["quote"][:80]) for c in draft["citations"]],
        questions=draft["clarifying_questions"],
    )
    raw = extract_json(llm.converse(SYSTEM, prompt, max_tokens=400))
    scores = {
        key: max(1, min(5, int(raw.get(key, 0) or 0)))
        for key in ("grounding", "cause_plausibility", "actionability")
    }
    scores["rationale"] = str(raw.get("rationale", ""))
    return scores
