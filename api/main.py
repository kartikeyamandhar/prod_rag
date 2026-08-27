"""FastAPI surface: one ticket in, one structured first response out.

Run: uv run --env-file .env uvicorn api.main:app --port 8080
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException

from api.pipeline import FirstResponse, TicketIn, handle_ticket
from retrieval.embedder import get_query_embedder

logger = logging.getLogger(__name__)

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _state["database_url"] = os.environ["DATABASE_URL"]
    _state["model"] = get_query_embedder(os.environ["EMBED_MODEL_NAME"])
    if os.environ.get("BEDROCK_ENABLED", "0").lower() in ("1", "true", "yes"):
        from api.llm import BedrockLLM

        _state["llm"] = BedrockLLM()
        logger.info("api ready, bedrock ENABLED", extra={"model_id": _state["llm"].model_id})
    else:
        _state["llm"] = None
        logger.info("api ready, stub mode")
    yield
    _state.clear()


app = FastAPI(title="rag-incident-lab", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/tickets")
def tickets(ticket: TicketIn) -> FirstResponse:
    from tickets.corpus_rules import TENANT_COUNT

    if not 0 <= ticket.tenant_id < TENANT_COUNT:
        raise HTTPException(status_code=422, detail=f"tenant_id must be in [0, {TENANT_COUNT})")
    if not ticket.title.strip():
        raise HTTPException(status_code=422, detail="title must be non-empty")
    try:
        # autocommit: the request path is read-only SELECTs; without it, psycopg
        # opens a transaction at the first query that then idles across the
        # multi-second Bedrock call, pinning MVCC snapshots and blocking vacuum.
        with psycopg.connect(_state["database_url"], connect_timeout=5, autocommit=True) as conn:
            return handle_ticket(conn, _state["model"], ticket, llm=_state.get("llm"))
    except psycopg.OperationalError as exc:
        logger.error("database unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="database unavailable") from exc
