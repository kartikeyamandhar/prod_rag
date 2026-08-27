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

# Kubernetes minor versions are 1.two-digits; \b keeps "took 1.5 seconds" out.
_VERSION_PATTERN = re.compile(r"\bv?1\.\d{2}\b")
DRAFT_TEXT_CHARS = 1600  # per-item context slice shared with the judge


class Citation(BaseModel):
    source: str  # corpus-tagged key
    url: str | None
    quote: str


class Draft(BaseModel):
    # None on extractive/degraded drafts: only the LLM drafter self-assesses.
    context_sufficiency: int | None = None
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


def compute_retrieval_confidence(items: list[RetrievedItem]) -> float:
    """Deterministic retrieval-quality signal with real variance (replaces the
    constant-0.5 ceiling ratio, audit A6):
    0.5 * cross-list agreement in the top-k (item found by >=2 searches)
    + 0.3 * any FTS list contributed at all
    + 0.2 * normalized score margin of top-1 over the tail."""
    if not items:
        return 0.0
    agreement = sum(1 for item in items if len(item.ranks) >= 2) / len(items)
    fts_contributed = float(any("_fts" in name for item in items for name in item.ranks))
    margin = (items[0].score - items[-1].score) / items[0].score if items[0].score else 0.0
    return 0.5 * agreement + 0.3 * fts_contributed + 0.2 * min(1.0, margin)


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
    from api.metrics import observe_degrade, observe_ticket
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
            observe_degrade("triage", exc)
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
            observe_degrade("draft", exc)
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
    retrieval_confidence = round(compute_retrieval_confidence(items), 3)
    route = decide_route(
        triage_confidence=triage.confidence,
        retrieval_confidence=retrieval_confidence,
        has_citations=bool(draft.citations),
        body_chars=len(ticket.body),
        degraded=draft_degraded,
        context_sufficiency=draft.context_sufficiency,
    )
    timings["draft_and_gate"] = round((time.perf_counter() - t0) * 1000, 2)
    observe_ticket(route.route, degraded, timings)

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
