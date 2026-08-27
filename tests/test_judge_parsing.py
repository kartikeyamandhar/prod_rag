"""Judge v2 score parsing: strict, never fabricates (audit C9/A2 regression tests)."""

from __future__ import annotations

import pytest

from probes.judge import parse_judge_scores


def test_valid_scores_pass_through() -> None:
    raw = {"grounding": 4, "cause_plausibility": 3, "actionability": 5, "rationale": "solid"}
    scores = parse_judge_scores(raw)
    assert (scores["grounding"], scores["cause_plausibility"], scores["actionability"]) == (4, 3, 5)
    assert scores["rationale"] == "solid"


def test_missing_key_raises_never_clamps() -> None:
    # The old clamp turned a missing field into a real-looking 1/5 (audit C9).
    with pytest.raises(ValueError, match="cause_plausibility"):
        parse_judge_scores({"grounding": 4, "actionability": 5})


def test_string_score_raises() -> None:
    with pytest.raises(ValueError, match="grounding"):
        parse_judge_scores({"grounding": "4/5", "cause_plausibility": 3, "actionability": 2})


def test_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match="actionability"):
        parse_judge_scores({"grounding": 4, "cause_plausibility": 3, "actionability": 0})
    with pytest.raises(ValueError, match="grounding"):
        parse_judge_scores({"grounding": 6, "cause_plausibility": 3, "actionability": 2})


def test_bool_is_not_a_score() -> None:
    # bool is an int subclass; True must not pass as 1.
    with pytest.raises(ValueError, match="grounding"):
        parse_judge_scores({"grounding": True, "cause_plausibility": 3, "actionability": 2})


def test_render_cited_accepts_pipeline_retrieval_shape() -> None:
    # cited_context comes from FirstResponse.retrieval entries, which key on
    # "key" (not "source"); a shape drift here crashes every judged run.
    from probes.judge import _render_cited

    entry = {"corpus": "docs", "key": "docs:41", "title": "DNS", "score": 0.03, "text": "x" * 700}
    rendered = _render_cited([entry])
    assert "[docs:41]" in rendered and len(rendered) < 700
    assert _render_cited([]) == "(the draft cited nothing)"
