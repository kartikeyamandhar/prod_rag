"""LLM triage via Bedrock: same Triage shape as the stub, real classification."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from api.llm import BedrockLLM, extract_json
from triage.stub import Triage

logger = logging.getLogger(__name__)

SYSTEM = (
    "You are the triage stage of a support system for a managed-Kubernetes platform"
    " vendor. Classify tickets precisely. Respond with strict JSON only, no prose."
)
PROMPT = """Classify this support ticket.

component: exactly one of "sig/network", "sig/scheduling", "sig/storage", "unknown".
severity: "high" (crash, panic, data loss, security), "medium" (degradation, leak,
timeout, wrong behavior), or "low" (cosmetic, question, minor).
evidence: up to 6 short terms from the ticket that drove your decision.
confidence: 0.0-1.0, your calibrated certainty in the component label.

Return JSON: {{"component": "...", "severity": "...", "evidence": [...], "confidence": 0.0}}

Ticket title: {title}
Ticket body:
{body}
"""

VALID_COMPONENTS = {"sig/network", "sig/scheduling", "sig/storage", "unknown"}


def parse_triage(raw: dict) -> Triage:
    if raw.get("component") not in VALID_COMPONENTS:
        raise ValueError(f"invalid component {raw.get('component')!r}")
    return Triage(
        component=raw["component"],
        severity=raw.get("severity", "low"),
        matched_terms=[str(term) for term in raw.get("evidence", [])][:6],
        confidence=max(0.0, min(1.0, float(raw.get("confidence", 0.0)))),
    )


def triage_llm(llm: BedrockLLM, title: str, body: str) -> Triage:
    prompt = PROMPT.format(title=title, body=body[:2000])
    for attempt in (1, 2):
        text = llm.converse(SYSTEM, prompt, max_tokens=300)
        try:
            return parse_triage(extract_json(text))
        except (ValueError, ValidationError, KeyError) as exc:
            logger.warning("triage parse failed (attempt %d): %s", attempt, exc)
    raise ValueError("triage output failed validation twice")
