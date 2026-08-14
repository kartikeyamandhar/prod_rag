"""Idempotent clone of kubernetes/website at the manifest-pinned SHA.

The manifest is the single source of truth: this script never chooses a SHA. It
clones (blob-filtered, sparse, full main history) if the cache is missing, then
checks out exactly corpus_manifest.toml's docs start_sha. Refuses to run unpinned.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from git import Repo

from ingest.manifest import load_manifest

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = REPO_ROOT / ".corpus_cache" / "website"
SPARSE_PATH = "content/en/docs/concepts"


def ensure_docs_checkout() -> Path:
    manifest = load_manifest(REPO_ROOT / "corpus_manifest.toml")
    docs = manifest.docs_corpus
    if not docs.start_sha:
        raise SystemExit("corpus_manifest.toml docs start_sha is unpinned; pin it first")

    if not CORPUS_DIR.exists():
        logger.info("cloning %s (blob-filtered, sparse)", docs.repo)
        repo = Repo.clone_from(
            docs.repo,
            CORPUS_DIR,
            multi_options=[
                "--filter=blob:none",
                "--sparse",
                "--single-branch",
                "--branch=main",
                "--no-tags",
            ],
        )
        repo.git.sparse_checkout("set", SPARSE_PATH)
    else:
        repo = Repo(CORPUS_DIR)

    if repo.head.commit.hexsha != docs.start_sha:
        logger.info("checking out pinned SHA %s", docs.start_sha)
        repo.git.checkout(docs.start_sha)
    subtree = CORPUS_DIR / SPARSE_PATH
    if not subtree.exists():
        raise SystemExit(f"sparse checkout missing expected subtree {subtree}")
    logger.info("docs checkout ready at %s @ %s", CORPUS_DIR, docs.start_sha)
    return subtree


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ensure_docs_checkout()
    sys.exit(0)
