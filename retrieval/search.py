"""Dual-corpus hybrid retrieval: pgvector + FTS per corpus, merged with RRF.

Four ranked lists feed one fusion: docs-vector, docs-FTS, tickets-vector,
tickets-FTS. Every result carries its corpus tag. Ticket retrieval enforces the
tenant filter (per-tenant private context) plus two held-out barriers: the flag
and the NULL embedding. The tenant_filter_enabled switch exists because incident 5
seeds a filter bug; flipping it is a logged decision, never a silent default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

logger = logging.getLogger(__name__)

RRF_K = 60
K_PER_LIST = 20
TOP_N = 8


@dataclass
class RetrievedItem:
    corpus: str  # "docs" | "tickets"
    key: str
    title: str
    context: str  # docs: section trail; tickets: SIG labels
    text: str  # FULL retrieved content; drafting and judging consume this
    snippet: str  # short display cut of text, never used for drafting
    url: str | None
    score: float
    ranks: dict[str, int]  # list name -> 1-based rank, for observability


def rrf_merge(
    ranked_lists: dict[str, list[tuple[str, dict]]], rrf_k: int = RRF_K, top_n: int = TOP_N
) -> list[RetrievedItem]:
    """Reciprocal rank fusion across named ranked lists of (key, payload)."""
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    payloads: dict[str, dict] = {}
    for list_name, entries in ranked_lists.items():
        for rank, (key, payload) in enumerate(entries, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            ranks.setdefault(key, {})[list_name] = rank
            payloads.setdefault(key, payload)
    ordered = sorted(scores, key=lambda key: (-scores[key], key))
    return [
        RetrievedItem(score=scores[key], ranks=ranks[key], key=key, **payloads[key])
        for key in ordered[:top_n]
    ]


def _docs_vector(cur: psycopg.Cursor, qvec: list[float], k: int) -> list[tuple[str, dict]]:
    cur.execute(
        "SELECT c.id, p.path, p.title, c.section, c.text"
        " FROM chunks c JOIN pages p ON p.id = c.page_id"
        " ORDER BY c.embedding <=> %s::vector LIMIT %s",
        (qvec, k),
    )
    return [
        (
            f"docs:{row[0]}",
            {
                "corpus": "docs",
                "title": row[2],
                "context": row[3],
                "text": row[4],
                "snippet": row[4][:200],
                "url": row[1],
            },
        )
        for row in cur.fetchall()
    ]


def _docs_fts(cur: psycopg.Cursor, query: str, k: int) -> list[tuple[str, dict]]:
    cur.execute(
        "SELECT c.id, p.path, p.title, c.section, c.text"
        " FROM chunks c JOIN pages p ON p.id = c.page_id,"
        " websearch_to_tsquery('english', %s) q"
        " WHERE c.tsv @@ q ORDER BY ts_rank_cd(c.tsv, q) DESC, c.id LIMIT %s",
        (query, k),
    )
    return [
        (
            f"docs:{row[0]}",
            {
                "corpus": "docs",
                "title": row[2],
                "context": row[3],
                "text": row[4],
                "snippet": row[4][:200],
                "url": row[1],
            },
        )
        for row in cur.fetchall()
    ]


def _ticket_filters(tenant_id: int, tenant_filter_enabled: bool) -> tuple[str, list]:
    clauses = ["embedding IS NOT NULL", "NOT is_held_out"]
    params: list = []
    if tenant_filter_enabled:
        clauses.append("tenant_id = %s")
        params.append(tenant_id)
    else:
        logger.warning("tenant filter DISABLED on ticket retrieval", extra={"tenant_id": tenant_id})
    return " AND ".join(clauses), params


def _tickets_vector(
    cur: psycopg.Cursor, qvec: list[float], tenant_id: int, enabled: bool, k: int
) -> list[tuple[str, dict]]:
    where, params = _ticket_filters(tenant_id, enabled)
    cur.execute(
        f"SELECT number, title, array_to_string(sigs, ','), embed_text, url"
        f" FROM tickets WHERE {where} ORDER BY embedding <=> %s::vector LIMIT %s",
        (*params, qvec, k),
    )
    return [
        (
            f"tickets:{row[0]}",
            {
                "corpus": "tickets",
                "title": row[1],
                "context": row[2],
                "text": row[3],
                "snippet": row[3][:200],
                "url": row[4],
            },
        )
        for row in cur.fetchall()
    ]


def _tickets_fts(
    cur: psycopg.Cursor, query: str, tenant_id: int, enabled: bool, k: int
) -> list[tuple[str, dict]]:
    where, params = _ticket_filters(tenant_id, enabled)
    cur.execute(
        f"SELECT number, title, array_to_string(sigs, ','), embed_text, url"
        f" FROM tickets, websearch_to_tsquery('english', %s) q"
        f" WHERE {where} AND tsv @@ q ORDER BY ts_rank_cd(tsv, q) DESC, number LIMIT %s",
        (query, *params, k),
    )
    return [
        (
            f"tickets:{row[0]}",
            {
                "corpus": "tickets",
                "title": row[1],
                "context": row[2],
                "text": row[3],
                "snippet": row[3][:200],
                "url": row[4],
            },
        )
        for row in cur.fetchall()
    ]


def hybrid_search(
    conn: psycopg.Connection,
    qvec: list[float],
    query: str,
    tenant_id: int,
    tenant_filter_enabled: bool = True,
    k_per_list: int = K_PER_LIST,
    top_n: int = TOP_N,
) -> list[RetrievedItem]:
    """One retrieval call: four ranked lists, one RRF fusion, corpus-tagged results."""
    with conn.cursor() as cur:
        lists = {
            "docs_vec": _docs_vector(cur, qvec, k_per_list),
            "docs_fts": _docs_fts(cur, query, k_per_list),
            "tickets_vec": _tickets_vector(cur, qvec, tenant_id, tenant_filter_enabled, k_per_list),
            "tickets_fts": _tickets_fts(cur, query, tenant_id, tenant_filter_enabled, k_per_list),
        }
    results = rrf_merge(lists, top_n=top_n)
    logger.info(
        "hybrid search done",
        extra={
            "tenant_id": tenant_id,
            "tenant_filter_enabled": tenant_filter_enabled,
            "list_sizes": {name: len(entries) for name, entries in lists.items()},
            "fused": len(results),
        },
    )
    return results
