"""LLM drafting via Bedrock: first response with span citations bound to retrieval.

Citation integrity is enforced, not trusted: every citation source must be one of
the retrieved item keys, and quotes are clipped to 140 chars. A draft that cites
outside its context fails validation and is retried once; persistent failure
raises so the caller degrades to the extractive draft.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from api.llm import BedrockLLM, extract_json
from api.pipeline import Citation, Draft
from retrieval.search import RetrievedItem

logger = logging.getLogger(__name__)

SYSTEM = (
    "You draft first responses for support tickets at a managed-Kubernetes platform"
    " vendor. Ground every claim in the provided context items. Never invent"
    " sources. Respond with strict JSON only, no prose."
)
PROMPT = """Draft a first response for this support ticket using ONLY the context items.

Return JSON:
{{"probable_cause": "1-3 sentences, grounded in the context",
  "suggested_fix": "1-4 sentences of concrete next steps, grounded in the context",
  "citations": [{{"source": "<key of a context item>", "quote": "<verbatim span, <=140 chars>"}}],
  "clarifying_questions": ["0-3 questions, only if information is genuinely missing"]}}

Rules: cite 2-4 items; "source" MUST be copied exactly from the context item keys;
quotes MUST be verbatim spans from that item's text. If the context does not
explain the ticket, say so in probable_cause and ask for what you need.

Ticket title: {title}
Ticket body:
{body}

Context items:
{context}
"""


# Per-item context budget: matches the ticket-corpus embed window, so the model
# drafts from what was actually indexed. 8 items x 1600 chars ~ 3.2k tokens.
DRAFT_CONTEXT_CHARS = 1600


def _render_context(items: list[RetrievedItem]) -> str:
    lines = []
    for item in items:
        lines.append(
            f"[{item.key}] ({item.corpus}) {item.title}\n  url: {item.url}\n"
            f"  text: {item.text[:DRAFT_CONTEXT_CHARS]}"
        )
    return "\n".join(lines)


def parse_draft(raw: dict, allowed_keys: set[str]) -> Draft:
    citations = []
    for entry in raw.get("citations", []):
        source = entry.get("source", "")
        if source not in allowed_keys:
            raise ValueError(f"citation outside retrieved context: {source!r}")
        citations.append(Citation(source=source, url=None, quote=str(entry.get("quote", ""))[:140]))
    if not citations:
        raise ValueError("draft has no valid citations")
    if not raw.get("probable_cause") or not raw.get("suggested_fix"):
        raise ValueError("draft missing probable_cause or suggested_fix")
    return Draft(
        probable_cause=str(raw["probable_cause"]),
        suggested_fix=str(raw["suggested_fix"]),
        citations=citations,
        clarifying_questions=[str(q) for q in raw.get("clarifying_questions", [])][:3],
    )


def draft_llm(llm: BedrockLLM, title: str, body: str, items: list[RetrievedItem]) -> Draft:
    allowed = {item.key for item in items}
    url_by_key = {item.key: item.url for item in items}
    prompt = PROMPT.format(title=title, body=body[:2000], context=_render_context(items))
    last_error: Exception | None = None
    for attempt in (1, 2):
        text = llm.converse(SYSTEM, prompt, max_tokens=1200, temperature=0.2)
        try:
            draft = parse_draft(extract_json(text), allowed)
            for citation in draft.citations:
                citation.url = url_by_key.get(citation.source)
            return draft
        except (ValueError, ValidationError, KeyError) as exc:
            last_error = exc
            logger.warning("draft parse failed (attempt %d): %s", attempt, exc)
    raise ValueError(f"draft output failed validation twice; last: {last_error}")
