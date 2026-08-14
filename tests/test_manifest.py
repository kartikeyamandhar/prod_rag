from pathlib import Path

import pytest

from ingest.manifest import CorpusManifest, ManifestError, load_manifest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_repo_manifest_loads_and_matches_pins() -> None:
    manifest = load_manifest(REPO_ROOT / "corpus_manifest.toml")
    assert isinstance(manifest, CorpusManifest)
    assert manifest.docs_corpus.subtree == "content/en/docs/concepts"
    assert manifest.docs_corpus.embed_model == "BAAI/bge-small-en-v1.5"
    assert manifest.docs_corpus.embed_dim == 384
    assert manifest.ticket_corpus.kind_filter == "kind/bug"
    assert manifest.ticket_corpus.resolution_linked is True
    assert manifest.ticket_corpus.tenant_count == 50
    # Unpinned until Phases 1a/1b; empty means unpinned, never a fake value.
    assert manifest.docs_corpus.start_sha == ""
    assert manifest.ticket_corpus.snapshot_date == ""


def test_missing_manifest_raises_manifest_error(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not readable"):
        load_manifest(tmp_path / "does_not_exist.toml")
