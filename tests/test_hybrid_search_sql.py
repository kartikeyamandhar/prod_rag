"""hybrid_search SQL against the real DB: tenant filter, held-out barrier (C7).

Integration tests; skipped without DATABASE_URL (run with --env-file .env).
The tenant filter is the incident-5 mechanism and was previously fully
untested; the held-out barrier is what keeps evaluation tickets out of every
retrieval result.
"""

from __future__ import annotations

import os

import psycopg
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs local DB (run with --env-file .env)"
)

QUERY = "kube-proxy conntrack entries leak after LoadBalancer service deletion"


@pytest.fixture(scope="module")
def conn():
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        yield connection


@pytest.fixture(scope="module")
def qvec():
    from retrieval.embedder import embed_query, get_query_embedder

    return embed_query(get_query_embedder(os.environ["EMBED_MODEL_NAME"]), QUERY)


def _ticket_tenants(conn) -> dict[int, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT number, tenant_id FROM tickets")
        return dict(cur.fetchall())


def test_tenant_filter_on_returns_only_own_tenant(conn, qvec) -> None:
    from retrieval.search import hybrid_search

    tenants = _ticket_tenants(conn)
    items = hybrid_search(conn, qvec, QUERY, tenant_id=41, tenant_filter_enabled=True)
    assert items, "retrieval returned nothing"
    ticket_items = [i for i in items if i.corpus == "tickets"]
    for item in ticket_items:
        assert tenants[int(item.key.split(":")[1])] == 41, f"foreign tenant in {item.key}"


def test_tenant_filter_off_leaks_foreign_tenants(conn, qvec) -> None:
    from retrieval.search import hybrid_search

    tenants = _ticket_tenants(conn)
    items = hybrid_search(conn, qvec, QUERY, tenant_id=41, tenant_filter_enabled=False)
    foreign = [
        i for i in items if i.corpus == "tickets" and tenants[int(i.key.split(":")[1])] != 41
    ]
    # ~50 tenants share the corpus: a tenant-blind search MUST surface foreign rows.
    assert foreign, "filter-off returned no foreign rows; the demo mechanism is broken"


@pytest.mark.parametrize("enabled", [True, False])
def test_held_out_tickets_never_retrieved(conn, qvec, enabled) -> None:
    from retrieval.search import hybrid_search

    with conn.cursor() as cur:
        cur.execute("SELECT number FROM tickets WHERE is_held_out")
        held_out = {row[0] for row in cur.fetchall()}
    assert held_out, "no held-out tickets in DB"
    items = hybrid_search(conn, qvec, QUERY, tenant_id=41, tenant_filter_enabled=enabled)
    for item in items:
        if item.corpus == "tickets":
            assert int(item.key.split(":")[1]) not in held_out


def test_fts_list_respects_tenant_filter(conn) -> None:
    from retrieval.search import _tickets_fts

    tenants = _ticket_tenants(conn)
    with conn.cursor() as cur:
        rows = _tickets_fts(cur, QUERY, tenant_id=41, enabled=True, k=8)
    for key, _payload in rows:
        assert tenants[int(key.split(":")[1])] == 41
