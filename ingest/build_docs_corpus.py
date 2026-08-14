"""Build the docs corpus artifact: walk the pinned checkout, chunk, embed, write parquet.

Laptop-side only (CLAUDE.md: heavy work stays off the box). Produces two artifacts:
  artifacts/docs_pages_<sha7>.parquet   one row per page, including zero-chunk pages
  artifacts/docs_chunks_<sha7>.parquet  one row per chunk with its 384-d embedding
Prints the gate evidence: page count, chunk count, image-bearing page inventory.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from fastembed import TextEmbedding

from ingest.chunker import Page, chunk_page
from ingest.clone_docs import CORPUS_DIR, ensure_docs_checkout
from ingest.manifest import load_manifest

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = REPO_ROOT / "artifacts"


def collect_pages(subtree: Path) -> list[Page]:
    pages: list[Page] = []
    for md_path in sorted(subtree.rglob("*.md")):
        rel = md_path.relative_to(CORPUS_DIR).as_posix()
        page = chunk_page(rel, md_path.read_text(encoding="utf-8"))
        if page.draft:
            logger.info("skipping draft page %s", rel)
            continue
        pages.append(page)
    return pages


def embed_chunks(texts: list[str], model_name: str, expected_dim: int) -> np.ndarray:
    model = TextEmbedding(model_name=model_name)
    vectors = np.array(list(model.embed(texts, batch_size=64)), dtype=np.float32)
    if vectors.shape != (len(texts), expected_dim):
        raise RuntimeError(
            f"embedding shape {vectors.shape} != ({len(texts)}, {expected_dim}); "
            f"dimension drift would silently corrupt the index"
        )
    return vectors


def main() -> None:
    manifest = load_manifest(REPO_ROOT / "corpus_manifest.toml")
    docs = manifest.docs_corpus
    subtree = ensure_docs_checkout()
    sha7 = docs.start_sha[:7]

    pages = collect_pages(subtree)
    image_pages = [p.path for p in pages if p.image_refs]
    all_chunks = [(p, c) for p in pages for c in p.chunks]
    logger.info("collected %d pages, %d chunks", len(pages), len(all_chunks))

    oversized = [c for _, c in all_chunks if len(c.text) > 2400]
    for chunk in oversized:
        logger.info("oversized atomic chunk kept whole: %d chars", len(chunk.text))

    vectors = embed_chunks([c.text for _, c in all_chunks], docs.embed_model, docs.embed_dim)

    ARTIFACTS.mkdir(exist_ok=True)
    pages_table = pa.table(
        {
            "path": [p.path for p in pages],
            "title": [p.title for p in pages],
            "has_images": [bool(p.image_refs) for p in pages],
            "n_chunks": [len(p.chunks) for p in pages],
            "start_sha": [docs.start_sha] * len(pages),
        }
    )
    chunks_table = pa.table(
        {
            "page_path": [p.path for p, _ in all_chunks],
            "section": [c.section for _, c in all_chunks],
            "chunk_index": [c.index for _, c in all_chunks],
            "text": [c.text for _, c in all_chunks],
            "n_chars": [len(c.text) for _, c in all_chunks],
            "embedding": pa.array(vectors.tolist(), type=pa.list_(pa.float32(), docs.embed_dim)),
            "embed_model": [docs.embed_model] * len(all_chunks),
            "start_sha": [docs.start_sha] * len(all_chunks),
        }
    )
    pages_path = ARTIFACTS / f"docs_pages_{sha7}.parquet"
    chunks_path = ARTIFACTS / f"docs_chunks_{sha7}.parquet"
    pq.write_table(pages_table, pages_path)
    pq.write_table(chunks_table, chunks_path)

    print(f"ARTIFACT pages_parquet={pages_path.name} rows={pages_table.num_rows}")
    print(f"ARTIFACT chunks_parquet={chunks_path.name} rows={chunks_table.num_rows}")
    print(f"IMAGE-BEARING PAGES: {len(image_pages)} of {len(pages)}")
    for path in image_pages:
        print(f"  {path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    main()
