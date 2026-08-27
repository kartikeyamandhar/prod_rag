from tickets.corpus_rules import (
    TENANT_COUNT,
    has_images,
    is_held_out,
    make_embed_text,
    tenant_of,
)


def test_tenant_assignment_is_deterministic_and_bounded() -> None:
    assert tenant_of(114514) == tenant_of(114514)
    assert all(0 <= tenant_of(n) < TENANT_COUNT for n in range(1, 2000))


def test_tenant_assignment_covers_all_tenants() -> None:
    seen = {tenant_of(n) for n in range(1, 2000)}
    assert seen == set(range(TENANT_COUNT))


def test_held_out_split_is_deterministic_and_near_ten_percent() -> None:
    flags = [is_held_out(n) for n in range(1, 10001)]
    assert flags == [is_held_out(n) for n in range(1, 10001)]
    fraction = sum(flags) / len(flags)
    assert 0.07 < fraction < 0.13


def test_image_detection_on_ticket_bodies() -> None:
    assert has_images("see ![topology](https://user-images.githubusercontent.com/x/y.png)")
    assert has_images("attached: https://github.com/user-attachments/assets/abc123")
    assert has_images('<img src="diagram.svg">')
    assert not has_images("plain text with a link https://example.com/page")
    assert not has_images(None)
    assert not has_images("")


def test_embed_text_strips_fences_and_truncates() -> None:
    body = "context before\n```\nE0815 kubelet panic stacktrace line\n```\nafter"
    text = make_embed_text("kubelet crashes on restart", body)
    assert "kubelet crashes on restart" in text
    assert "stacktrace" not in text
    assert "context before" in text
    assert "after" in text
    long_body = "word " * 2000
    assert len(make_embed_text("t", long_body)) == 1600


def test_captioned_embed_text_budget_partition() -> None:
    from tickets.corpus_rules import make_captioned_embed_text

    # Audit A14: a long body must survive long captions (fixed budgets, no eviction).
    text = make_captioned_embed_text("X" * 500, "Y" * 5000, "Z" * 2000)
    assert text.count("X") == 200
    assert text.count("Z") == 400
    assert text.count("Y") == 1200
    # No captions: no empty "Image content:" stanza.
    assert "Image content" not in make_captioned_embed_text("title", "body", "")
