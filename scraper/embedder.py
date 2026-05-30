"""Keyless local embeddings via fastembed (BAAI/bge-small-en-v1.5, 384 dim).

No API key, no billing, no per-call cost — the ONNX model runs on the GitHub
Actions CPU runner. This replaces the OpenAI text-embedding-3-small path
(D-003 anticipated this exact fallback: "swap the embedder for a local
bge-small-en-v1.5 model at 384 dims and re-backfill"). The app's
filing_chunks.embedding column is migrated to vector(384) to match.

`embed_all` keeps the same signature and Optional[...] return type so
pipeline.py is unchanged; on any model failure it returns Nones and the row
inserts with a NULL embedding (hybrid_search falls back to Postgres FTS).
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384

_model = None  # lazily-instantiated fastembed.TextEmbedding
_load_failed = False


def _get_model():
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    try:
        from fastembed import TextEmbedding

        log.info("loading embedding model %s (first call downloads ONNX)", MODEL)
        _model = TextEmbedding(model_name=MODEL)
    except Exception as e:  # pragma: no cover
        log.warning("fastembed load failed (%s) — embeddings disabled this run", e)
        _load_failed = True
        _model = None
    return _model


def embed_all(
    texts: list[str], batch_size: Optional[int] = None
) -> list[Optional[list[float]]]:
    """Return one 384-float embedding per input text. Returns Nones (same
    length) if the model can't load, so callers still pair chunks with rows.
    """
    if not texts:
        return []
    model = _get_model()
    if model is None:
        return [None] * len(texts)
    try:
        # fastembed yields numpy arrays in input order.
        vecs = list(model.embed(texts))
        out: list[Optional[list[float]]] = []
        for v in vecs:
            arr = v.tolist() if hasattr(v, "tolist") else list(v)
            out.append([float(x) for x in arr])
        if len(out) != len(texts):
            log.warning(
                "embed count mismatch (%d vs %d) — padding with None",
                len(out),
                len(texts),
            )
            while len(out) < len(texts):
                out.append(None)
        return out
    except Exception as e:
        log.warning("embed_all failed (%s) — inserting NULL embeddings", e)
        return [None] * len(texts)
