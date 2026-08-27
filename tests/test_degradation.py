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


def test_triage_failure_does_not_disable_llm_draft(monkeypatch) -> None:
    """A10: latches are independent; a triage hiccup must not degrade the draft."""
    import psycopg

    import triage.llm_triage as lt
    from api import draft_llm as dl
    from api.pipeline import Draft, TicketIn, handle_ticket
    from retrieval.embedder import get_query_embedder

    def failing_triage(llm, title, body):
        raise LLMUnavailable("ThrottlingException")

    marker = Draft(
        probable_cause="llm-drafted",
        suggested_fix="llm-drafted",
        citations=[],
        clarifying_questions=[],
    )

    def fake_draft(llm, title, body, items):
        return marker.model_copy(deep=True)

    monkeypatch.setattr(lt, "triage_llm", failing_triage)
    monkeypatch.setattr(dl, "draft_llm", fake_draft)

    embedder = get_query_embedder(os.environ.get("EMBED_MODEL_NAME", "BAAI/bge-small-en-v1.5"))
    ticket = TicketIn(title="scheduler preemption stuck pending", body="b" * 200, tenant_id=3)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        response = handle_ticket(conn, embedder, ticket, llm=ThrottledLLM())

    assert response.draft.probable_cause == "llm-drafted"  # draft stage still ran LLM
    assert response.degraded is True  # triage degraded -> API flag set
    assert response.degrade_reasons and response.degrade_reasons[0].startswith("triage:")
    # Draft was NOT degraded, so the gate must not force escalate for degradation.
    assert not any("degraded" in r for r in response.route.reasons)


def test_request_path_holds_no_open_transaction_during_llm_call() -> None:
    """A7: retrieval must not pin an MVCC snapshot across the Bedrock call."""
    import psycopg

    from api.pipeline import TicketIn, handle_ticket
    from retrieval.embedder import get_query_embedder

    observed: list[object] = []

    class TxProbeLLM:
        model_id = "probe"

        def __init__(self, conn: object) -> None:
            self._conn = conn

        def converse(self, *args: object, **kwargs: object) -> str:
            observed.append(self._conn.info.transaction_status)
            raise LLMUnavailable("probe-done")

    embedder = get_query_embedder(os.environ.get("EMBED_MODEL_NAME", "BAAI/bge-small-en-v1.5"))
    ticket = TicketIn(title="csi volume mount timeout", body="b" * 200, tenant_id=18)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        handle_ticket(conn, embedder, ticket, llm=TxProbeLLM(conn))

    assert observed, "probe LLM was never called"
    assert all(status == psycopg.pq.TransactionStatus.IDLE for status in observed)
