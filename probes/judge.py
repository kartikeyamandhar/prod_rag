"""Rubric-anchored judge v2: scores a drafted first response against REAL evidence.

v2 fixes the two eval-validity holes the audit proved (B4/A2): the judge now
receives the closing PR's title and body (not a URL it cannot open) and the
actual cited context spans the drafter saw. Parse failures RAISE; a judge score
is never fabricated (the old clamp turned missing fields into real-looking 1s).
Judged numbers stay unpublishable before the human spot-check (CLAUDE.md).
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

Ground truth. The ticket's real SIG labels: {sigs}. The ACTUAL resolution
(closing PR the maintainers merged):
PR title: {pr_title}
PR description (excerpt):
{pr_body}

The context spans the drafter cited (verify quotes and support against these):
{cited_context}

Draft under evaluation:
context_sufficiency (draft's own claim): {sufficiency}
probable_cause: {probable_cause}
suggested_fix: {suggested_fix}
citations: {citations}
clarifying_questions: {questions}

Return JSON: {{"grounding": 1-5, "cause_plausibility": 1-5, "actionability": 1-5,
"rationale": "2-4 sentences"}}
"""

SCORE_KEYS = ("grounding", "cause_plausibility", "actionability")


def parse_judge_scores(raw: dict) -> dict:
    """Strict: all three keys present as ints in 1..5, else ValueError. Never clamp."""
    scores: dict = {}
    for key in SCORE_KEYS:
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            raise ValueError(f"judge score {key!r} missing or invalid: {value!r}")
        scores[key] = value
    scores["rationale"] = str(raw.get("rationale", ""))
    return scores


def _render_cited(cited_context: list[dict]) -> str:
    if not cited_context:
        return "(the draft cited nothing)"
    return "\n".join(
        f"[{entry['key']}] {entry.get('title', '')}\n  {entry.get('text', '')[:600]}"
        for entry in cited_context
    )


def judge_draft(llm: BedrockLLM, ticket_row: dict, draft: dict, cited_context: list[dict]) -> dict:
    pr_title = ticket_row.get("closing_pr_title") or "(resolution PR content unavailable)"
    pr_body = (ticket_row.get("closing_pr_body") or "")[:1500]
    prompt = PROMPT.format(
        title=ticket_row["title"],
        body=ticket_row["body"][:1500],
        sigs=ticket_row["sigs"],
        pr_title=pr_title,
        pr_body=pr_body or "(no description)",
        cited_context=_render_cited(cited_context),
        sufficiency=draft.get("context_sufficiency"),
        probable_cause=draft["probable_cause"],
        suggested_fix=draft["suggested_fix"],
        citations=[(c["source"], c["quote"][:80]) for c in draft["citations"]],
        questions=draft["clarifying_questions"],
    )
    last_error: Exception | None = None
    for attempt in (1, 2):
        text = llm.converse(SYSTEM, prompt, max_tokens=800)
        try:
            return parse_judge_scores(extract_json(text))
        except ValueError as exc:
            last_error = exc
            logger.warning("judge parse failed (attempt %d): %s", attempt, exc)
    raise ValueError(f"judge output failed validation twice; last: {last_error}")
