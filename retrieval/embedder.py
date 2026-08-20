"""Process-wide query embedder, lazily initialized and safe under threads.

The ONNX model costs ~1s to load; API request handlers must share one instance.
InferenceSession.run is thread-safe, so concurrent query embeddings need no lock;
only initialization is guarded.
"""

from __future__ import annotations

import logging
import threading

from fastembed import TextEmbedding

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_model: TextEmbedding | None = None


def get_query_embedder(model_name: str) -> TextEmbedding:
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                logger.info("loading query embedder %s", model_name)
                _model = TextEmbedding(model_name=model_name)
    return _model


def embed_query(model: TextEmbedding, query: str) -> list[float]:
    """Embed one query with the model's query-side prefix (bge convention)."""
    vector = next(iter(model.query_embed(query)))
    return vector.tolist()
