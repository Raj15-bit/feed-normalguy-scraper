"""Download PDF to RAM and extract per-page text with pymupdf (fitz)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 100  # below this we assume the PDF is image-only (scanned)


@dataclass
class PdfPage:
    page: int  # 1-based
    text: str


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, OSError)),
    reraise=True,
)
def download_pdf(url: str, timeout: float = 60.0) -> bytes:
    headers = {
        "User-Agent": (
            "feed-normalguy-scraper/1.0 (+https://feed.normalguy.co.in)"
        ),
        "Accept": "application/pdf,*/*;q=0.8",
    }
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content


def extract_pages(pdf_bytes: bytes) -> Optional[list[PdfPage]]:
    """Returns per-page text. Returns None if the PDF appears to be image-only
    (total text length < MIN_TEXT_CHARS). pymupdf imports as `fitz`.
    """
    import fitz  # pymupdf

    pages: list[PdfPage] = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        log.warning("pymupdf failed to open document: %s", e)
        return None
    try:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            pages.append(PdfPage(page=i, text=text.strip()))
    finally:
        doc.close()

    total = sum(len(p.text) for p in pages)
    if total < MIN_TEXT_CHARS:
        log.info("skipping scanned/image PDF (%d chars total)", total)
        return None
    return pages
