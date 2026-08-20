from retrieval.search import RetrievedItem, rrf_merge

PAYLOAD_A = {"corpus": "docs", "title": "A", "context": "", "snippet": "", "url": None}
PAYLOAD_B = {"corpus": "tickets", "title": "B", "context": "", "snippet": "", "url": None}
PAYLOAD_C = {"corpus": "docs", "title": "C", "context": "", "snippet": "", "url": None}


def test_item_in_two_lists_outranks_single_list_winner() -> None:
    lists = {
        "vec": [("a", PAYLOAD_A), ("b", PAYLOAD_B)],
        "fts": [("c", PAYLOAD_C), ("a", PAYLOAD_A)],
    }
    merged = rrf_merge(lists, top_n=3)
    assert merged[0].key == "a"
    assert merged[0].ranks == {"vec": 1, "fts": 2}


def test_scores_follow_reciprocal_rank_formula() -> None:
    lists = {"vec": [("a", PAYLOAD_A)], "fts": [("a", PAYLOAD_A)]}
    merged = rrf_merge(lists, rrf_k=60, top_n=1)
    assert abs(merged[0].score - 2 / 61) < 1e-12


def test_top_n_truncates_and_ties_break_deterministically() -> None:
    lists = {"vec": [("b", PAYLOAD_B), ("a", PAYLOAD_A)]}
    merged_full = rrf_merge(lists, top_n=5)
    assert [item.key for item in merged_full] == ["b", "a"]
    tie_lists = {"vec": [("b", PAYLOAD_B)], "fts": [("a", PAYLOAD_A)]}
    merged_tie = rrf_merge(tie_lists, top_n=2)
    # Equal scores: deterministic lexicographic key order, never dict order.
    assert [item.key for item in merged_tie] == ["a", "b"]
    assert all(isinstance(item, RetrievedItem) for item in merged_tie)
