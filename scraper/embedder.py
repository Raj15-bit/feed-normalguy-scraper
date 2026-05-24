"""Batch OpenAI embeddings (text-embedding-3-small, 1536 dim per D-003)."""
from __future__ import annotations

import logging
from typing import Optional

from openai import OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scraper.config import get_config

log = logging.getLogger(__name__)

MODEL = "text-embedding-3-small"

_client: Optional[OpenAI] = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=get_config().openai_api_key)
    return _client


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _embed_batch(texts: list[str]) -> list[list[float]]:
    res = _openai().embeddings.create(model=MODEL, input=texts)
    return [d.embedding for d in res.data]


def embed_all(texts: list[str], batch_size: Optional[int] = None) -> list[list[float]]:
    if not texts:
        return []
    batch_size = batch_size or get_config().batch_size_embeddings
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        log.info("embedding batch %d/%d (size=%d)",
                 i // batch_size + 1,
                 (len(texts) + batch_size - 1) // batch_size,
                 len(chunk))
        out.extend(_embed_batch(chunk))
    return out
