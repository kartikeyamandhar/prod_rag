from updater.replayer import parse_name_status

SUBTREE = "content/en/docs/concepts"


def test_modified_added_deleted_parsed() -> None:
    diff = (
        f"M\t{SUBTREE}/workloads/pods/_index.md\n"
        f"A\t{SUBTREE}/security/new-page.md\n"
        f"D\t{SUBTREE}/storage/old-page.md\n"
    )
    changes = parse_name_status(diff)
    assert [(c.change_type, c.path) for c in changes] == [
        ("modified", f"{SUBTREE}/workloads/pods/_index.md"),
        ("added", f"{SUBTREE}/security/new-page.md"),
        ("deleted", f"{SUBTREE}/storage/old-page.md"),
    ]


def test_rename_within_subtree_keeps_old_path() -> None:
    diff = f"R097\t{SUBTREE}/a.md\t{SUBTREE}/b.md\n"
    changes = parse_name_status(diff)
    assert len(changes) == 1
    assert changes[0].change_type == "renamed"
    assert changes[0].path == f"{SUBTREE}/b.md"
    assert changes[0].old_path == f"{SUBTREE}/a.md"


def test_rename_out_of_subtree_is_a_delete() -> None:
    diff = f"R100\t{SUBTREE}/a.md\tcontent/en/docs/tasks/a.md\n"
    changes = parse_name_status(diff)
    assert [(c.change_type, c.path) for c in changes] == [("deleted", f"{SUBTREE}/a.md")]


def test_non_subtree_and_non_markdown_filtered() -> None:
    diff = (
        f"M\tcontent/en/docs/tasks/debug.md\nM\t{SUBTREE}/architecture/diagram.svg\nM\tREADME.md\n"
    )
    assert parse_name_status(diff) == []
