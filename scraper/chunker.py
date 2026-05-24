"""Split per-page filing text into ~800-token chunks with 100-token overlap.

Page boundaries are preserved — a chunk never spans multiple pages, so the
`page` cited by the RAG layer is always accurate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import tiktoken

from scraper.pdf_extract import PdfPage

log = logging.getLogger(__name__)

ENC = tiktoken.get_encoding("cl100k_base")  # close enough to embedding tokenizer

TARGET_TOKENS = 800
OVERLAP_TOKENS = 100


@dataclass
class Chunk:
    page: int
    text: str


def chunk_pages(pages: Iterable[PdfPage]) -> list[Chunk]:
    out: list[Chunk] = []
    for page in pages:
        if not page.text.strip():
            continue
        tokens = ENC.encode(page.text)
        if len(tokens) <= TARGET_TOKENS:
            out.append(Chunk(page=page.page, text=page.text.strip()))
            continue
        # Slide a window of TARGET_TOKENS with OVERLAP_TOKENS step-back.
        step = TARGET_TOKENS - OVERLAP_TOKENS
        for start in range(0, len(tokens), step):
            window = tokens[start : start + TARGET_TOKENS]
            if not window:
                break
            text = ENC.decode(window).strip()
            if text:
                out.append(Chunk(page=page.page, text=text))
            if start + TARGET_TOKENS >= len(tokens):
                break
    return out
