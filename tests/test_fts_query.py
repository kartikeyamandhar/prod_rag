import os

import pytest

from retrieval.search import build_fts_query


def test_long_title_becomes_or_query() -> None:
    q = build_fts_query("AssumePod on scheduler's snapshot doesn't modify snapshotted affinity")
    assert " | " in q
    assert "assumepod" in q
    assert " & " not in q  # AND-semantics is the measured failure mode


def test_lexical_payload_extracted_from_body() -> None:
    q = build_fts_query(
        "pods stuck pending\n"
        "Run `kubectl describe pod` and check --feature-gates flag; the "
        'endpointSliceTracker misses io.kubernetes.pod events "connection refused"'
    )
    for expected in (
        "kubectl",
        "feature",
        "gates",
        "endpointslicetracker",
        "kubernetes",
        "refused",
    ):
        assert expected in q, q


def test_injection_characters_sanitized_and_capped() -> None:
    q = build_fts_query("a&b | c!d:(e) 'quote' " + " ".join(f"word{i}" for i in range(30)))
    assert all(ch not in q for ch in "&!:()'\"")
    assert len(q.split(" | ")) <= 15


def test_stopword_only_title_yields_empty() -> None:
    assert build_fts_query("what is the and of it") == ""


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs local DB")
def test_conntrack_title_returns_docs_rows() -> None:
    """A4 regression: the 9-word storm-payload title returned 0 FTS rows."""
    import psycopg

    from retrieval.search import _docs_fts

    title = "kube-proxy conntrack entries leak after LoadBalancer service deletion"
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        assert len(_docs_fts(cur, title, 20)) > 0
