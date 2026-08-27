"""The drafting prompt must carry full retrieved text, not a 200-char cut (A1/B3)."""

from api.draft_llm import DRAFT_CONTEXT_CHARS, _render_context
from retrieval.search import RetrievedItem


def make_item(text: str) -> RetrievedItem:
    return RetrievedItem(
        corpus="docs",
        key="docs:1",
        title="Persistent Volumes",
        context="Reclaiming",
        text=text,
        snippet=text[:200],
        url="content/en/docs/concepts/storage/persistent-volumes.md",
        score=0.0164,
        ranks={"docs_vec": 1},
    )


def test_answer_past_char_200_reaches_the_prompt() -> None:
    filler = "x" * 900
    answer = "The finalizer kubernetes.io/pv-protection must be removed after detach."
    item = make_item(filler + " " + answer)
    rendered = _render_context([item])
    assert answer in rendered  # pre-fix code rendered only the first 200 chars


def test_context_is_budgeted_not_unbounded() -> None:
    item = make_item("y" * 20_000)
    rendered = _render_context([item])
    assert len(rendered) < DRAFT_CONTEXT_CHARS + 300  # budget + header slack
