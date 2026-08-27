"""Dual-corpus hybrid retrieval: pgvector + FTS per corpus, merged with RRF.

Four ranked lists feed one fusion: docs-vector, docs-FTS, tickets-vector,
tickets-FTS. FTS queries are OR-ified (title terms + lexical payload tokens
from the body) because AND-semantics over long natural-language queries
returns zero rows, silently reducing hybrid search to vector-only; that
failure was measured; legacy mode survives only for A/B probes.
Every result carries its corpus tag. Ticket retrieval enforces the
tenant filter (per-tenant private context) plus two held-out barriers: the flag
and the NULL embedding. The tenant_filter_enabled switch exists because incident 5
seeds a filter bug; flipping it is a logged decision, never a silent default.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import psycopg

logger = logging.getLogger(__name__)

RRF_K = 60
K_PER_LIST = 20
TOP_N = 8

_STOPWORDS = frozenset(
    "a an and are as at be been by can cannot do does for from has have how in is it its"
    " may more most no not of on or should that the their then there these this to was were"
    " what when where which while why will with you your after before during between".split()
)
_LEXICAL_PAYLOAD = re.compile(
    r"`([^`\n]{2,60})`"  # backticked identifiers
    r"|(--[A-Za-z][A-Za-z0-9-]{2,40})"  # CLI flags
    r"|\b([a-z][a-z0-9]*(?:[A-Z][a-z0-9]+)+)\b"  # CamelCase identifiers
    r"|\b([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+){1,5})\b"  # dotted.paths
    r'|"([^"\n]{4,60})"'  # quoted error strings
)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


def build_fts_query(query: str, max_terms: int = 15) -> str:
    """OR-ified tsquery: title words plus high-signal lexical tokens from the body.

    The first line of `query` is treated as the title (always included, minus
    stopwords); the remainder contributes only tokens FTS exists to catch and
    vectors blur: backticked names, flags, CamelCase, dotted paths, quoted
    errors. Terms are normalized to bare lexeme-safe words and OR-joined.
    """
    title, _, body = query.partition("\n")
    terms: list[str] = []

    def add(raw: str) -> None:
        for piece in re.split(r"[^A-Za-z0-9_]+", raw.lower()):
            if len(piece) >= 3 and piece not in _STOPWORDS and piece not in terms:
                terms.append(piece)

    for word in _WORD.findall(title.lower()):
        if word not in _STOPWORDS:
            add(word)
    for match in _LEXICAL_PAYLOAD.finditer(body[:2000]):
        token = next(group for group in match.groups() if group)
        add(token)
    return " | ".join(terms[:max_terms])


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


def _docs_fts(
    cur: psycopg.Cursor, query: str, k: int, fts_mode: str = "lexical"
) -> list[tuple[str, dict]]:
    if fts_mode == "lexical":
        fts_query = build_fts_query(query)
        if not fts_query:
            return []
        fn = "to_tsquery"
    else:  # legacy: AND-semantics, kept for A/B probes only
        fts_query, fn = query, "websearch_to_tsquery"
    cur.execute(
        "SELECT c.id, p.path, p.title, c.section, c.text"
        " FROM chunks c JOIN pages p ON p.id = c.page_id,"
        f" {fn}('english', %s) q"
        " WHERE c.tsv @@ q ORDER BY ts_rank_cd(c.tsv, q) DESC, c.id LIMIT %s",
        (fts_query, k),
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


def _ticket_filters(tenant_id: int, tenant_filter_enabled: bool) -> tuple[str, dict]:
    """Named parameters throughout: clause order can never silently re-bind a
    value to the wrong placeholder (audit C7: the positional form put the FTS
    query before the tenant filter purely by textual position)."""
    clauses = ["embedding IS NOT NULL", "NOT is_held_out"]
    params: dict = {}
    if tenant_filter_enabled:
        clauses.append("tenant_id = %(tenant_id)s")
        params["tenant_id"] = tenant_id
    else:
        logger.warning("tenant filter DISABLED on ticket retrieval", extra={"tenant_id": tenant_id})
    return " AND ".join(clauses), params


def _tickets_vector(
    cur: psycopg.Cursor, qvec: list[float], tenant_id: int, enabled: bool, k: int
) -> list[tuple[str, dict]]:
    where, params = _ticket_filters(tenant_id, enabled)
    cur.execute(
        f"SELECT number, title, array_to_string(sigs, ','), embed_text, url"
        f" FROM tickets WHERE {where}"
        f" ORDER BY embedding <=> %(qvec)s::vector LIMIT %(k)s",
        {**params, "qvec": qvec, "k": k},
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
    cur: psycopg.Cursor,
    query: str,
    tenant_id: int,
    enabled: bool,
    k: int,
    fts_mode: str = "lexical",
) -> list[tuple[str, dict]]:
    if fts_mode == "lexical":
        fts_query = build_fts_query(query)
        if not fts_query:
            return []
        fn = "to_tsquery"
    else:
        fts_query, fn = query, "websearch_to_tsquery"
    where, params = _ticket_filters(tenant_id, enabled)
    cur.execute(
        f"SELECT number, title, array_to_string(sigs, ','), embed_text, url"
        f" FROM tickets, {fn}('english', %(q)s) q"
        f" WHERE {where} AND tsv @@ q ORDER BY ts_rank_cd(tsv, q) DESC, number LIMIT %(k)s",
        {**params, "q": fts_query, "k": k},
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
    fts_mode: str = "lexical",
) -> list[RetrievedItem]:
    """One retrieval call: four ranked lists, one RRF fusion, corpus-tagged results."""
    with conn.cursor() as cur:
        lists = {
            "docs_vec": _docs_vector(cur, qvec, k_per_list),
            "docs_fts": _docs_fts(cur, query, k_per_list, fts_mode=fts_mode),
            "tickets_vec": _tickets_vector(cur, qvec, tenant_id, tenant_filter_enabled, k_per_list),
            "tickets_fts": _tickets_fts(
                cur, query, tenant_id, tenant_filter_enabled, k_per_list, fts_mode=fts_mode
            ),
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
