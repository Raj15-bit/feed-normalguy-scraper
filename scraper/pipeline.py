"""Per-filing pipeline reused by main.py (cron) and backfill.py (one-shot).

Steps:
  1. Skip if slug already exists (dedup by MD5 of source URL).
  2. Download PDF to RAM, extract text per page.
  3. Skip if text is too short (scanned image).
  4. Classify label (mapping → regex → DeepSeek).
  5. Chunk into ~800-token blocks preserving page boundaries.
  6. Embed all chunks in batches.
  7. Insert filing row, then chunks.
  8. Queue alert fanout to watchers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from scraper.bse_client import Announcement
from scraper.chunker import chunk_pages
from scraper.classifier import classify_multi, _pick_primary
from scraper.config import get_config
from scraper.db import (
    Company,
    filing_exists,
    filing_slug_for,
    insert_chunks,
    insert_filing,
    queue_alerts,
    upsert_content_cache,
)
from scraper.doc_period import classify_doc, decide_period
from scraper.embedder import embed_all
from scraper.pdf_extract import download_pdf, extract_pages
from scraper.summarizer import summarize
from scraper.transcript_detect import (
    looks_like_transcript,
    normalize_transcript_title,
)

log = logging.getLogger(__name__)

# Don't spend a DeepSeek summary call on thin one-line notices (newspaper
# publications, trading-window intimations, etc.) — the card just shows the
# title for these. Only summarise filings with substantial extracted text.
SUMMARY_MIN_BODY_CHARS = 1_200


@dataclass
class PipelineResult:
    status: str  # 'inserted' | 'skipped_existing' | 'skipped_scanned' | 'failed'
    filing_id: str | None = None
    chunks: int = 0
    alerts_queued: int = 0
    error: str | None = None


def process_announcement(
    *, company: Company, ann: Announcement
) -> PipelineResult:
    slug = filing_slug_for(ann.source_url)
    try:
        if filing_exists(slug):
            return PipelineResult(status="skipped_existing")

        pdf_bytes = download_pdf(ann.source_url)
        pages = extract_pages(pdf_bytes)
        if pages is None:
            return PipelineResult(status="skipped_scanned")

        # Body text reused for both labeling (DeepSeek) and summarisation.
        body = "\n\n".join(p.text for p in pages if p.text)

        labels, label = classify_multi(
            title=ann.title,
            bse_category=ann.bse_category,
            bse_subcategory=ann.bse_subcategory,
            body=body,
        )

        # Transcript detection (title OR body). A transcript is always a concall
        # and must land in the Concalls section, so we force the concall label
        # and normalise the title to carry "Transcript" — even when the filing
        # was posted under a generic "Con. Call Updates" notice with the
        # transcript attached. Non-transcript concall notices are left untouched
        # (they stay out of the Concall section, in "Other").
        store_title = ann.title
        if looks_like_transcript(ann.title, body):
            if "concall" not in labels:
                labels = (["concall"] + labels)[:4]
            label = "concall"
            store_title = normalize_transcript_title(ann.title)
        elif "concall" in labels:
            # DEMOTE: a non-transcript notice ("Con. Call Updates", analyst-meet
            # intimation, "attend conference") must NOT carry the concall label.
            # The Concalls surface is transcripts-only. Drop concall and fall
            # back to the next-best label (or 'other').
            labels = [l for l in labels if l != "concall"] or ["other"]
            label = _pick_primary(labels)

        # Decide the document TYPE + fiscal PERIOD once, from the body, and store
        # them (migration 0014) so the app reads columns instead of re-guessing.
        doc_kind, is_transcript = classify_doc(store_title, body, label)
        fiscal_year, fiscal_quarter, period_source = decide_period(
            title=store_title,
            body=body,
            posted_at=ann.posted_at,
            source_url=ann.source_url,
            doc_kind=doc_kind,
        )

        chunks = chunk_pages(pages)
        if not chunks:
            return PipelineResult(status="skipped_scanned")

        embeddings = embed_all([c.text for c in chunks])

        ai_headline: str | None = None
        ai_bullets: list[str] | None = None
        if get_config().summary_enabled and len(body) >= SUMMARY_MIN_BODY_CHARS:
            try:
                ai_headline, ai_bullets = summarize(
                    title=ann.title,
                    label=label,
                    body=body,
                    bse_category=ann.bse_category,
                    bse_subcategory=ann.bse_subcategory,
                )
            except Exception as e:
                log.warning("summarize failed for %s: %s", ann.source_url, e)

        filing_id = insert_filing(
            company_id=company.id,
            slug=slug,
            title=store_title,
            label=label,
            labels=labels,
            source_url=ann.source_url,
            posted_at=ann.posted_at,
            page_count=len(pages),
            bse_category=ann.bse_category,
            bse_subcategory=ann.bse_subcategory,
            ai_headline=ai_headline,
            ai_summary_bullets=ai_bullets,
            doc_kind=doc_kind,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            period_source=period_source,
            is_transcript=is_transcript,
        )
        insert_chunks(
            filing_id=filing_id,
            company_id=company.id,
            chunks=[
                {"page": c.page, "text": c.text, "embedding": emb}
                for c, emb in zip(chunks, embeddings)
            ],
        )
        # Pre-warm the app's content cache so the AI chat reads instantly
        # (no live PDF download at chat time). Best-effort.
        upsert_content_cache(source_url=ann.source_url, extracted_text=body)

        alerts = 0
        try:
            alerts = queue_alerts(filing_id)
        except Exception as e:
            log.warning("queue_alerts failed for %s: %s", filing_id, e)
        log.info(
            "inserted filing=%s company=%s chunks=%d label=%s alerts=%d",
            filing_id,
            company.slug,
            len(chunks),
            label,
            alerts,
        )
        return PipelineResult(
            status="inserted",
            filing_id=filing_id,
            chunks=len(chunks),
            alerts_queued=alerts,
        )
    except Exception as e:
        log.exception("process_announcement failed for %s: %s", ann.source_url, e)
        return PipelineResult(status="failed", error=str(e))
