"""Process-wide query embedder, lazily initialized and safe under threads.

The ONNX model costs ~1s to load; request handlers share one instance.
InferenceSession.run is thread-safe, so concurrent embeddings need no lock;
only initialization is guarded.

Honesty note (verified against fastembed 0.8.0): for BAAI/bge-small-en-v1.5,
TextEmbedding.query_embed is identical to embed; NO query-side prefix is applied
by the library. The bge query instruction was A/B tested on the replay harness
(artifacts/r2_fts_ab.json, 2026-08-27): identical same-SIG@8, docs-domain@8,
and MRR with and without it on this workload, so per the pre-registered rule it
is NOT applied. Queries and passages share one embedding convention.
"""

from __future__ import annotations

import logging
import threading

from fastembed import TextEmbedding

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_model: TextEmbedding | None = None
_model_name: str | None = None


def get_query_embedder(model_name: str) -> TextEmbedding:
    global _model, _model_name
    if _model is None:
        with _lock:
            if _model is None:
                logger.info("loading embedder %s", model_name)
                _model = TextEmbedding(model_name=model_name)
                _model_name = model_name
    if model_name != _model_name:
        # Same-dimension mismatches would silently corrupt retrieval; refuse.
        raise ValueError(f"embedder already loaded as {_model_name!r}, requested {model_name!r}")
    return _model


def embed_query(model: TextEmbedding, query: str) -> list[float]:
    """Embed one query; same convention as passages (see module doc for why)."""
    vector = next(iter(model.embed([query])))
    return vector.tolist()
