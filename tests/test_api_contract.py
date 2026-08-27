"""API contract via TestClient: the schema k6 and the replayer probes depend on.

Integration tests (lifespan opens a real pool); skipped without DATABASE_URL.
"""

from __future__ import annotations

import os

import psycopg
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs local DB (run with --env-file .env)"
)

TICKET = {
    "title": "kube-proxy conntrack entries leak after LoadBalancer service deletion",
    "body": "After deleting a LoadBalancer service on v1.31, DNS lookups fail"
    " intermittently. Stale conntrack entries point at removed endpoints."
    " Reproduced on three clusters.",
    "tenant_id": 41,
}


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_healthz_does_a_real_db_check(client) -> None:
    assert client.get("/healthz").json() == {"ok": True}


def test_metrics_exposes_all_families(client) -> None:
    client.post("/tickets", json=TICKET)
    body = client.get("/metrics").text
    for family in (
        "rag_tickets_total",
        "rag_stage_seconds",
        "rag_degrade_total",
        "rag_admission_rejects_total",
        "rag_bedrock_tokens_total",
    ):
        assert f"# HELP {family}" in body, f"{family} missing from /metrics"


def test_tenant_bound_and_empty_title_are_422(client) -> None:
    assert client.post("/tickets", json={**TICKET, "tenant_id": 50}).status_code == 422
    assert client.post("/tickets", json={**TICKET, "tenant_id": -1}).status_code == 422
    assert client.post("/tickets", json={**TICKET, "title": "  "}).status_code == 422


def test_ticket_response_schema_contract(client) -> None:
    response = client.post("/tickets", json=TICKET)
    assert response.status_code == 200
    data = response.json()
    assert data["route"]["route"] in ("auto_attach", "escalate", "request_info")
    assert isinstance(data["degraded"], bool)
    assert isinstance(data["degrade_reasons"], list)
    assert data["retrieval"], "no retrieval items"
    for entry in data["retrieval"]:
        assert {"corpus", "key", "title", "score", "url", "text"} <= set(entry)
    assert "context_sufficiency" in data["draft"]
    assert set(data["timings_ms"]) == {"triage", "retrieval", "draft_and_gate"}


def test_db_down_is_503_not_500(client) -> None:
    from api import main

    class DeadPool:
        def connection(self, timeout=None):
            raise psycopg.OperationalError("simulated: database unavailable")

    real_pool = main._state["pool"]
    main._state["pool"] = DeadPool()
    try:
        assert client.post("/tickets", json=TICKET).status_code == 503
        assert client.get("/healthz").status_code == 503
    finally:
        main._state["pool"] = real_pool
