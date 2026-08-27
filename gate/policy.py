"""Confidence gate: the only component called an agent in this system.

Routes every drafted first response to exactly one of {auto_attach, escalate,
request_info}. Pure function of its inputs, threshold-based, fully unit-tested.
Hard rules run before thresholds: an information-starved ticket cannot be
auto-answered, and a draft without citations must never auto-attach.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

AUTO_ATTACH_MIN = 0.65
REQUEST_INFO_MAX = 0.35
MIN_BODY_CHARS = 80

Route = Literal["auto_attach", "escalate", "request_info"]


class RouteDecision(BaseModel):
    route: Route
    confidence: float
    reasons: list[str]


def decide_route(
    triage_confidence: float,
    retrieval_confidence: float,
    has_citations: bool,
    body_chars: int,
    degraded: bool = False,
    context_sufficiency: int | None = None,
) -> RouteDecision:
    """Every route decision flows through here, degraded paths included, so the
    hard rules and the audit log hold on every request.

    context_sufficiency is the draft's own 1-5 signal about its context; a draft
    that says it cannot answer (<=2) must never auto-attach, and what it needs
    is more information, so it routes request-info. This kills the failure class
    where drafts openly admitting ignorance were auto-attached (audit B2)."""
    if context_sufficiency is None:
        confidence = round(0.5 * triage_confidence + 0.5 * retrieval_confidence, 3)
    else:
        confidence = round(
            0.4 * triage_confidence + 0.3 * retrieval_confidence + 0.3 * (context_sufficiency / 5),
            3,
        )
    reasons = [
        f"triage_confidence={triage_confidence:.2f}",
        f"retrieval_confidence={retrieval_confidence:.2f}",
        f"context_sufficiency={context_sufficiency}",
    ]

    if body_chars < MIN_BODY_CHARS:
        reasons.append(f"body {body_chars} chars < {MIN_BODY_CHARS}: information-starved")
        decision = RouteDecision(route="request_info", confidence=confidence, reasons=reasons)
    elif degraded:
        reasons.append("degraded: retrieval-only draft, never auto-attached")
        decision = RouteDecision(route="escalate", confidence=confidence, reasons=reasons)
    elif context_sufficiency is not None and context_sufficiency <= 2:
        reasons.append("draft self-assessed context as insufficient (<=2): ask the customer")
        decision = RouteDecision(route="request_info", confidence=confidence, reasons=reasons)
    elif not has_citations:
        reasons.append("draft has no citations: never auto-attach uncited advice")
        decision = RouteDecision(route="escalate", confidence=confidence, reasons=reasons)
    elif confidence >= AUTO_ATTACH_MIN:
        reasons.append(f"confidence {confidence:.2f} >= {AUTO_ATTACH_MIN}")
        decision = RouteDecision(route="auto_attach", confidence=confidence, reasons=reasons)
    elif confidence < REQUEST_INFO_MAX:
        reasons.append(f"confidence {confidence:.2f} < {REQUEST_INFO_MAX}")
        decision = RouteDecision(route="request_info", confidence=confidence, reasons=reasons)
    else:
        reasons.append(f"confidence {confidence:.2f} in escalate band")
        decision = RouteDecision(route="escalate", confidence=confidence, reasons=reasons)

    logger.info(
        "gate routed",
        extra={"route": decision.route, "confidence": confidence, "reasons": reasons},
    )
    return decision
