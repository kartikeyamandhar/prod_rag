"""FastAPI surface: one ticket in, one structured first response out.

Run: uv run --env-file .env uvicorn api.main:app --port 8080
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import psycopg
from anyio import to_thread
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from psycopg_pool import ConnectionPool

from api.pipeline import FirstResponse, TicketIn, handle_ticket
from retrieval.embedder import get_query_embedder

logger = logging.getLogger(__name__)

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Explicit thread cap: this WAS implicitly 40 (AnyIO default), which acted
    # as the de facto admission policy and allowed 40 concurrent DB connects.
    to_thread.current_default_thread_limiter().total_tokens = 16
    # Pooled connections, autocommit (the request path is read-only SELECTs;
    # without autocommit a transaction idles across the multi-second Bedrock
    # call, pinning MVCC snapshots and blocking vacuum). open+wait so a booting
    # box fails fast and systemd restarts us once Postgres is up.
    pool = ConnectionPool(
        conninfo=os.environ["DATABASE_URL"],
        min_size=2,
        max_size=8,
        kwargs={"autocommit": True},
        open=False,
    )
    pool.open(wait=True, timeout=30)
    _state["pool"] = pool
    _state["model"] = get_query_embedder(os.environ["EMBED_MODEL_NAME"])
    if os.environ.get("BEDROCK_ENABLED", "0").lower() in ("1", "true", "yes"):
        from api.llm import BedrockLLM

        _state["llm"] = BedrockLLM()
        logger.info("api ready, bedrock ENABLED", extra={"model_id": _state["llm"].model_id})
    else:
        _state["llm"] = None
        logger.info("api ready, stub mode")
    yield
    pool.close()
    _state.clear()


app = FastAPI(title="rag-incident-lab", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict:
    try:
        with _state["pool"].connection(timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # any pool/DB failure means not healthy
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"ok": True}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/tickets")
def tickets(ticket: TicketIn) -> FirstResponse:
    from tickets.corpus_rules import TENANT_COUNT

    if not 0 <= ticket.tenant_id < TENANT_COUNT:
        raise HTTPException(status_code=422, detail=f"tenant_id must be in [0, {TENANT_COUNT})")
    if not ticket.title.strip():
        raise HTTPException(status_code=422, detail="title must be non-empty")
    try:
        with _state["pool"].connection(timeout=5) as conn:
            return handle_ticket(conn, _state["model"], ticket, llm=_state.get("llm"))
    except (psycopg.OperationalError, TimeoutError) as exc:
        logger.error("database unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="database unavailable") from exc
