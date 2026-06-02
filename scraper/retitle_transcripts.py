"""One-off (and repeatable) pass: surface transcripts already in the DB.

Many transcripts were stored under a generic "Con. Call Updates" title with the
transcript PDF attached, so they never matched the app's title-based Concall
filter. This scans every concall-labelled filing, reads the text we extracted
(filing_content_cache; falls back to a live download), and when the body is
actually a transcript it re-titles the row to carry "Transcript" and pins the
primary label to concall — so it shows up in the Concalls section.

Idempotent: rows whose title already says "Transcript" are skipped. Safe to run
daily/weekly. Usage:  python -m scraper.retitle_transcripts [--max N]
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from scraper.config import get_config, setup_logging
from scraper.db import (
    fetch_concall_candidates,
    get_cached_text,
    update_filing_label_title,
)
from scraper.pdf_extract import download_pdf, extract_pages
from scraper.transcript_detect import (
    TRANSCRIPT_TITLE_RE,
    is_intimation_title,
    looks_like_transcript,
    normalize_transcript_title,
)

log = logging.getLogger("scraper.retitle_transcripts")

_PAGE = 200


def _body_for(source_url: str, allow_download: bool) -> str | None:
    text = get_cached_text(source_url)
    if text:
        return text
    if not allow_download:
        return None
    try:
        pages = extract_pages(download_pdf(source_url))
        if not pages:
            return None
        return "\n\n".join(p.text for p in pages if p.text)
    except Exception as e:  # noqa: BLE001 — best effort
        log.warning("download failed for %s: %s", source_url, e)
        return None


def run(max_rows: int | None = None, allow_download: bool = True) -> int:
    setup_logging()
    get_config()  # validate env early
    stats: Counter[str] = Counter()
    offset = 0
    fixed = 0
    while True:
        rows = fetch_concall_candidates(limit=_PAGE, offset=offset)
        if not rows:
            break
        for r in rows:
            stats["scanned"] += 1
            title = r.get("title") or ""
            if TRANSCRIPT_TITLE_RE.search(title):
                stats["already_transcript"] += 1
                continue
            # A pre-event notice ("X to Announce Results on <date>") is the
            # intimation letter, not a transcript — never promote it, even if
            # its body happens to mention "transcript" / "earnings call".
            if is_intimation_title(title):
                stats["intimation_skipped"] += 1
                continue
            body = _body_for(r["source_url"], allow_download)
            if not looks_like_transcript(title, body):
                stats["not_transcript"] += 1
                continue
            labels = list(r.get("labels") or [])
            if "concall" not in labels:
                labels = (["concall"] + labels)[:4]
            try:
                update_filing_label_title(
                    filing_id=r["id"],
                    title=normalize_transcript_title(title),
                    label="concall",
                    labels=labels,
                )
                fixed += 1
                stats["retitled"] += 1
                log.info("retitled transcript id=%s title=%r", r["id"], title)
            except Exception as e:  # noqa: BLE001
                log.warning("update failed id=%s: %s", r["id"], e)
                stats["update_failed"] += 1
            if max_rows and fixed >= max_rows:
                log.info("max reached (%d), stopping", max_rows)
                log.info("retitle complete %s", dict(stats))
                return 0
        offset += _PAGE
    log.info("retitle complete %s", dict(stats))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="scraper.retitle_transcripts")
    p.add_argument("--max", type=int, default=None, help="cap rows re-titled")
    p.add_argument(
        "--no-download",
        action="store_true",
        help="only use cached text; skip live PDF downloads",
    )
    args = p.parse_args()
    return run(max_rows=args.max, allow_download=not args.no_download)


if __name__ == "__main__":
    sys.exit(main())
