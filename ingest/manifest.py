"""Typed loader for corpus_manifest.toml, the provenance source of truth for both corpora."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class ManifestError(Exception):
    """Raised when the corpus manifest is missing, unreadable, or fails schema validation."""


class DocsCorpusManifest(BaseModel):
    repo: str
    subtree: str
    license: str
    attribution: str
    start_sha: str
    pinned_date: str
    embed_model: str
    embed_dim: int


class TicketCorpusManifest(BaseModel):
    repo: str
    snapshot_date: str
    sigs: list[str]
    kind_filter: str
    resolution_linked: bool
    tenant_count: int
    held_out_count: int
    train_count: int


class CorpusManifest(BaseModel):
    docs_corpus: DocsCorpusManifest
    ticket_corpus: TicketCorpusManifest


def load_manifest(path: Path) -> CorpusManifest:
    """Load and validate the corpus manifest at ``path``.

    Raises ManifestError on a missing file, invalid TOML, or a schema mismatch.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManifestError(f"corpus manifest not readable at {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ManifestError(f"corpus manifest at {path} is not valid TOML: {exc}") from exc
    try:
        manifest = CorpusManifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestError(f"corpus manifest at {path} failed schema validation: {exc}") from exc
    logger.info(
        "corpus manifest loaded",
        extra={
            "manifest_path": str(path),
            "docs_start_sha": manifest.docs_corpus.start_sha or "<unpinned>",
            "ticket_snapshot": manifest.ticket_corpus.snapshot_date or "<unpinned>",
        },
    )
    return manifest
