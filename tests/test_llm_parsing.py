import pytest

from api.draft_llm import parse_draft
from api.llm import extract_json
from triage.llm_triage import parse_triage


def test_extract_json_from_fenced_response() -> None:
    text = 'Here you go:\n```json\n{"a": {"b": 1}, "c": [2, 3]}\n```\nDone.'
    assert extract_json(text) == {"a": {"b": 1}, "c": [2, 3]}


def test_extract_json_rejects_garbage_and_unbalanced() -> None:
    with pytest.raises(ValueError):
        extract_json("no json here")
    with pytest.raises(ValueError):
        extract_json('{"open": {')


def test_parse_triage_validates_component() -> None:
    good = parse_triage(
        {"component": "sig/network", "severity": "high", "evidence": ["dns"], "confidence": 1.7}
    )
    assert good.component == "sig/network"
    assert good.confidence == 1.0  # clamped
    with pytest.raises(ValueError):
        parse_triage({"component": "sig/docs", "severity": "low"})


def test_parse_draft_rejects_citation_outside_context() -> None:
    raw = {
        "probable_cause": "x",
        "suggested_fix": "y",
        "citations": [{"source": "docs:999999", "quote": "z"}],
    }
    with pytest.raises(ValueError, match="outside retrieved context"):
        parse_draft(raw, allowed_keys={"docs:1", "tickets:2"})


def test_parse_draft_accepts_valid_and_clips_quote() -> None:
    raw = {
        "probable_cause": "cause",
        "suggested_fix": "fix",
        "citations": [{"source": "docs:1", "quote": "q" * 500}],
        "clarifying_questions": ["a", "b", "c", "d"],
    }
    draft = parse_draft(raw, allowed_keys={"docs:1"})
    assert len(draft.citations[0].quote) == 140
    assert len(draft.clarifying_questions) == 3
