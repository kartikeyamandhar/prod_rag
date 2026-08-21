"""Incident 6 path: Bedrock throttle degrades to retrieval-only draft + escalate.

Integration test against the local DB and embedder; skipped when DATABASE_URL is
absent (run via: uv run --env-file .env pytest tests/test_degradation.py).
"""

import os

import pytest

from api.llm import LLMUnavailable

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs local DB (run with --env-file .env)"
)


class ThrottledLLM:
    """Stands in for BedrockLLM under a hard throttle."""

    model_id = "fake"

    def converse(self, *args: object, **kwargs: object) -> str:
        raise LLMUnavailable("ThrottlingException")


def test_throttled_bedrock_degrades_to_extractive_escalate() -> None:
    import psycopg

    from api.pipeline import TicketIn, handle_ticket
    from retrieval.embedder import get_query_embedder

    embedder = get_query_embedder(os.environ.get("EMBED_MODEL_NAME", "BAAI/bge-small-en-v1.5"))
    ticket = TicketIn(
        title="kube-proxy conntrack entries leak after LoadBalancer service deletion",
        body="After deleting a LoadBalancer service on v1.31, DNS lookups fail"
        " intermittently. Stale conntrack entries point at removed endpoints." * 3,
        tenant_id=41,
    )
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        response = handle_ticket(conn, embedder, ticket, llm=ThrottledLLM())

    assert response.degraded is True
    assert response.route.route == "escalate"
    assert any("degraded" in reason for reason in response.route.reasons)
    # Retrieval-only draft still carries citations: degraded, not empty-handed.
    assert response.draft.citations
    assert response.retrieval
