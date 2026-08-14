from ingest.chunker import MAX_CHUNK_CHARS, chunk_page

FIXTURE = """---
title: Pods
draft: false
---

<!-- overview -->

A {{< glossary_tooltip text="Pod" term_id="pod" >}} is the smallest deployable unit.

{{< note >}}
Pods are ephemeral by design.
{{< /note >}}

![pod lifecycle](/images/pod-lifecycle.svg)

## Pod phases

| Phase | Meaning |
| ----- | ------- |
| Pending | Accepted, not yet running |
| Running | Bound and started |

## Templating example

```yaml
metadata:
  name: {{ .Values.name }}
```
"""


def test_title_and_draft_parsed() -> None:
    page = chunk_page("concepts/workloads/pods/_index.md", FIXTURE)
    assert page.title == "Pods"
    assert page.draft is False


def test_glossary_tooltip_replaced_by_text() -> None:
    page = chunk_page("p.md", FIXTURE)
    intro = page.chunks[0].text
    assert "A Pod is the smallest deployable unit." in intro
    assert "glossary_tooltip" not in intro


def test_note_shortcode_stripped_body_kept() -> None:
    page = chunk_page("p.md", FIXTURE)
    joined = "\n".join(chunk.text for chunk in page.chunks)
    assert "Pods are ephemeral by design." in joined
    assert "{{<" not in joined.replace("{{ .Values", "")


def test_table_stays_whole_in_one_chunk() -> None:
    page = chunk_page("p.md", FIXTURE)
    holders = [c for c in page.chunks if "| Pending |" in c.text]
    assert len(holders) == 1
    assert "| Running |" in holders[0].text
    assert holders[0].section == "Pod phases"


def test_code_fence_templating_survives() -> None:
    page = chunk_page("p.md", FIXTURE)
    joined = "\n".join(chunk.text for chunk in page.chunks)
    assert "{{ .Values.name }}" in joined


def test_image_detected_from_raw_source() -> None:
    page = chunk_page("p.md", FIXTURE)
    assert page.image_refs == ["/images/pod-lifecycle.svg"]


def test_chunk_indexes_are_page_wide_and_ordered() -> None:
    page = chunk_page("p.md", FIXTURE)
    assert [c.index for c in page.chunks] == list(range(len(page.chunks)))


def test_long_section_splits_under_limit() -> None:
    paragraphs = "\n\n".join(f"Paragraph number {i}. " + "text " * 60 for i in range(12))
    source = f"---\ntitle: Long\n---\n\n## Big section\n\n{paragraphs}\n"
    page = chunk_page("long.md", source)
    assert len(page.chunks) > 1
    assert all(len(c.text) <= MAX_CHUNK_CHARS + 200 for c in page.chunks)
