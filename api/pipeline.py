"""Pipeline orchestration: triage -> retrieval -> extractive draft -> gate.

The draft is extractive (span citations copied verbatim from retrieved items),
the local stand-in for Bedrock drafting. Retrieval confidence is a stub proxy:
top fused score normalized against the two-list-agreement ceiling.
"""

from __future__ import annotations

import logging
import os
import re
import time

import psycopg
from fastembed import TextEmbedding
from pydantic import BaseModel

from gate.policy import RouteDecision, decide_route
from retrieval.embedder import embed_query
from retrieval.search import RetrievedItem, hybrid_search
from triage.stub import Triage, triage_ticket

logger = logging.getLogger(__name__)

# Perfect two-list agreement at rank 1 scores 2/61; top-1 in one list scores 1/61.
# KNOWN DEGENERACY (replaced in gate v2): with empty FTS lists this ceiling makes
# retrieval confidence a constant 0.5.
RETRIEVAL_SCORE_CEILING = 2 / 61
# Kubernetes minor versions are 1.two-digits; \b keeps "took 1.5 seconds" out.
_VERSION_PATTERN = re.compile(r"\bv?1\.\d{2}\b")
DRAFT_TEXT_CHARS = 1600  # per-item context slice shared with the judge


class Citation(BaseModel):
    source: str  # corpus-tagged key
    url: str | None
    quote: str


class Draft(BaseModel):
    probable_cause: str
    suggested_fix: str
    citations: list[Citation]
    clarifying_questions: list[str]


class TicketIn(BaseModel):
    title: str
    body: str = ""
    tenant_id: int


class FirstResponse(BaseModel):
    triage: Triage
    retrieval: list[dict]
    draft: Draft
    route: RouteDecision
    degraded: bool = False
    degrade_reasons: list[str] = []
    timings_ms: dict[str, float]


def build_draft(items: list[RetrievedItem], title: str, body: str) -> Draft:
    docs = [item for item in items if item.corpus == "docs"][:3]
    tickets = [item for item in items if item.corpus == "tickets"][:2]

    if tickets:
        probable_cause = (
            f"Closest resolved ticket in scope: '{tickets[0].title.strip()}'"
            f" ({tickets[0].url}). The failure mode is likely related."
        )
    elif docs:
        probable_cause = f"No similar resolved ticket; nearest doc topic: {docs[0].title}."
    else:
        probable_cause = "No relevant context retrieved."

    if docs:
        suggested_fix = (
            f"Per '{docs[0].title}' ({docs[0].context or 'overview'}): {docs[0].text.strip()[:400]}"
        )
    else:
        suggested_fix = "Insufficient documentation context; see clarifying questions."

    citations = [
        Citation(source=item.key, url=item.url, quote=item.text.strip()[:140])
        for item in docs + tickets
    ]

    clarifying_questions: list[str] = []
    if not _VERSION_PATTERN.search(f"{title} {body}"):
        clarifying_questions.append("Which Kubernetes version (e.g. 1.31) are you running?")
    if "```" not in body:
        clarifying_questions.append("Can you attach relevant logs or error output?")
    if len(body) < 200:
        clarifying_questions.append("What exact steps reproduce the failure?")

    return Draft(
        probable_cause=probable_cause,
        suggested_fix=suggested_fix,
        citations=citations,
        clarifying_questions=clarifying_questions,
    )


def handle_ticket(
    conn: psycopg.Connection,
    model: TextEmbedding,
    ticket: TicketIn,
    tenant_filter_enabled: bool = True,
    llm: object | None = None,
) -> FirstResponse:
    # Deferred import: draft_llm imports this module's response models.
    from api.draft_llm import draft_llm
    from api.llm import LLMPermanentError, LLMUnavailable
    from triage.llm_triage import triage_llm

    timings: dict[str, float] = {}
    # Independent latches: a triage failure must not disable LLM drafting.
    triage_degraded = False
    draft_degraded = False
    degrade_reasons: list[str] = []

    t0 = time.perf_counter()
    if llm is not None:
        try:
            triage = triage_llm(llm, ticket.title, ticket.body)  # type: ignore[arg-type]
        except (LLMUnavailable, LLMPermanentError, ValueError) as exc:
            if os.environ.get("DEGRADE_DISABLED", "0") == "1":
                raise  # incident 6 "before": the naive system without a degrade path
            reason = f"triage: {type(exc).__name__}: {exc}"
            degrade_reasons.append(reason)
            level = logging.ERROR if isinstance(exc, LLMPermanentError) else logging.WARNING
            logger.log(level, "triage degraded to stub", extra={"reason": reason})
            triage_degraded = True
            triage = triage_ticket(ticket.title, ticket.body)
    else:
        triage = triage_ticket(ticket.title, ticket.body)
    timings["triage"] = round((time.perf_counter() - t0) * 1000, 2)

    t0 = time.perf_counter()
    query = f"{ticket.title}\n{ticket.body[:500]}"
    qvec = embed_query(model, query)
    items = hybrid_search(
        conn, qvec, query, ticket.tenant_id, tenant_filter_enabled=tenant_filter_enabled
    )
    timings["retrieval"] = round((time.perf_counter() - t0) * 1000, 2)

    t0 = time.perf_counter()
    if llm is not None:
        try:
            draft = draft_llm(llm, ticket.title, ticket.body, items)  # type: ignore[arg-type]
        except (LLMUnavailable, LLMPermanentError, ValueError) as exc:
            if os.environ.get("DEGRADE_DISABLED", "0") == "1":
                raise
            reason = f"draft: {type(exc).__name__}: {exc}"
            degrade_reasons.append(reason)
            level = logging.ERROR if isinstance(exc, LLMPermanentError) else logging.WARNING
            logger.log(level, "draft degraded to extractive", extra={"reason": reason})
            draft_degraded = True
            draft = build_draft(items, ticket.title, ticket.body)
    else:
        draft = build_draft(items, ticket.title, ticket.body)

    # API field: degraded if ANY stage degraded. Gate hard rule: only a degraded
    # DRAFT forces escalate (incident-6 invariant is about retrieval-only content
    # reaching a human); a stub triage alone does not taint an LLM-written draft.
    degraded = triage_degraded or draft_degraded
    retrieval_confidence = min(1.0, (items[0].score / RETRIEVAL_SCORE_CEILING) if items else 0.0)
    route = decide_route(
        triage_confidence=triage.confidence,
        retrieval_confidence=round(retrieval_confidence, 3),
        has_citations=bool(draft.citations),
        body_chars=len(ticket.body),
        degraded=draft_degraded,
    )
    timings["draft_and_gate"] = round((time.perf_counter() - t0) * 1000, 2)

    return FirstResponse(
        triage=triage,
        retrieval=[
            {
                "corpus": i.corpus,
                "key": i.key,
                "title": i.title,
                "score": round(i.score, 4),
                "url": i.url,
                "text": i.text[:DRAFT_TEXT_CHARS],
            }
            for i in items
        ],
        draft=draft,
        route=route,
        degraded=degraded,
        degrade_reasons=degrade_reasons,
        timings_ms=timings,
    )
