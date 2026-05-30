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


_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _headers_for(url: str) -> dict[str, str]:
    """BSE returns 403 to non-browser UAs, and 403 again unless Referer matches
    bseindia.com. NSE's archive subdomain (nsearchives.nseindia.com) similarly
    wants a Referer to nseindia.com."""
    base = {
        "User-Agent": _BROWSER_UA,
        "Accept": "application/pdf,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if "bseindia.com" in url:
        base["Referer"] = "https://www.bseindia.com/"
    elif "nseindia.com" in url:
        base["Referer"] = "https://www.nseindia.com/"
    return base


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, OSError)),
    reraise=True,
)
def download_pdf(url: str, timeout: float = 30.0) -> bytes:
    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout, connect=10.0),
        headers=_headers_for(url),
    ) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content


def extract_pages(pdf_bytes: bytes) -> Optional[list[PdfPage]]:
    """Returns per-page text. When the embedded text layer is too thin
    (total < MIN_TEXT_CHARS) and OCR is enabled in config, render each page
    and OCR via Tesseract. Returns None only if both paths produce nothing
    or the document fails to open. pymupdf imports as `fitz`.
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

        total = sum(len(p.text) for p in pages)
        if total >= MIN_TEXT_CHARS:
            return pages

        from scraper.config import get_config
        cfg = get_config()
        if not cfg.ocr_enabled:
            log.info("skipping scanned/image PDF (%d chars, OCR disabled)", total)
            return None

        ocr_pages = _ocr_document(doc, max_pages=cfg.ocr_max_pages, dpi=cfg.ocr_dpi)
    finally:
        doc.close()

    if ocr_pages is None:
        return None
    ocr_total = sum(len(p.text) for p in ocr_pages)
    if ocr_total < MIN_TEXT_CHARS:
        log.info("OCR produced too little text (%d chars), skipping", ocr_total)
        return None
    log.info("OCR recovered %d chars from scanned PDF", ocr_total)
    return ocr_pages


def _ocr_document(doc, *, max_pages: int, dpi: int) -> Optional[list[PdfPage]]:
    """Render each page to an image and run Tesseract on it. Skips pages
    beyond max_pages to bound runtime. Returns None on import/binary failure."""
    try:
        import io
        import pytesseract
        from PIL import Image
    except Exception as e:
        log.warning("OCR dependencies missing: %s", e)
        return None

    pages: list[PdfPage] = []
    n = min(len(doc), max_pages)
    zoom = dpi / 72.0
    import fitz
    matrix = fitz.Matrix(zoom, zoom)
    for i in range(n):
        try:
            pix = doc[i].get_pixmap(matrix=matrix, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(img) or ""
        except pytesseract.TesseractNotFoundError:
            log.warning("tesseract binary not installed; OCR aborted")
            return None
        except Exception as e:
            log.warning("OCR failed on page %d: %s", i + 1, e)
            text = ""
        pages.append(PdfPage(page=i + 1, text=text.strip()))
    return pages
