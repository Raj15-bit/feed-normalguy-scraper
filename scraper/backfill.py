"""One-time historical backfill. Usage:

    python -m scraper.backfill --days 365 [--company-slug reliance]
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from scraper.classifier import classify_via_mapping, classify_via_regex
from scraper.config import get_config, setup_logging
from scraper.db import list_companies
from scraper.pipeline import process_announcement
from scraper.sources import fetch_all_for

log = logging.getLogger("scraper.backfill")

# Title keyword patterns per target doc type — used so an oddly-titled real
# document (e.g. a transcript whose title the label-guess misses) still passes
# the doc-type pre-filter.
_TITLE_KEYWORDS: dict[str, re.Pattern[str]] = {
    "concall": re.compile(
        r"transcript|audio[\s-]*recording|earnings call|conference call|concall|investor call|analyst call|investor/?analyst meet|investor meet",
        re.I,
    ),
    "investor_ppt": re.compile(
        r"presentation|investor\s+update|earnings\s+update|investor\s+day|analyst\s+day|investor\s+deck|earnings\s+deck",
        re.I,
    ),
    "credit_rating": re.compile(
        r"\brating\b|crisil|icra|care ratings|care edge|moody|fitch|ind-?ra|s&p|s & p",
        re.I,
    ),
    "annual_report": re.compile(r"annual report", re.I),
}


def _title_hits(title: str, doc_types: set[str]) -> bool:
    for dt in doc_types:
        pat = _TITLE_KEYWORDS.get(dt)
        if pat and pat.search(title or ""):
            return True
    return False


def run(
    days: int,
    company_slug: str | None = None,
    doc_types: set[str] | None = None,
) -> int:
    setup_logging()
    cfg = get_config()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    companies = list_companies(only_with_bse=False)
    if company_slug:
        companies = [c for c in companies if c.slug == company_slug]
    log.info(
        "backfill since=%s companies=%d max_per_run=%d doc_types=%s",
        since.date().isoformat(),
        len(companies),
        cfg.max_filings_per_run,
        sorted(doc_types) if doc_types else "all",
    )
    statuses: Counter[str] = Counter()
    processed = 0
    for company in companies:
        if not (company.bse_code or company.nse_symbol):
            continue
        anns = fetch_all_for(company, since=since)
        log.info("company=%s anns=%d", company.slug, len(anns))
        for ann in anns:
            if processed >= cfg.max_filings_per_run:
                log.info("max_filings_per_run reached, stopping")
                break
            # Cheap pre-filter (no download/LLM) when focusing on doc types.
            # Keep if the label-guess matches OR the title positively mentions a
            # target doc (catches oddly-titled transcripts/presentations/etc.).
            if doc_types is not None:
                guess = classify_via_mapping(
                    ann.bse_category, ann.bse_subcategory
                ) or classify_via_regex(ann.title)
                if guess not in doc_types and not _title_hits(ann.title, doc_types):
                    statuses["skipped_filtered"] += 1
                    continue
            res = process_announcement(company=company, ann=ann)
            statuses[res.status] += 1
            processed += 1
        if processed >= cfg.max_filings_per_run:
            break
    log.info("backfill complete processed=%d %s", processed, dict(statuses))
    failed = statuses.get("failed", 0)
    if processed >= 5 and (failed / processed) > cfg.fail_threshold:
        log.error(
            "fail-loud: failure rate %.0f%% (%d/%d) exceeds threshold %.0f%%",
            100 * failed / processed, failed, processed, 100 * cfg.fail_threshold,
        )
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="scraper.backfill")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--company-slug", type=str, default=None)
    p.add_argument(
        "--doc-types",
        type=str,
        default=None,
        help="CSV of label slugs to focus on, e.g. concall,investor_ppt,credit_rating,annual_report",
    )
    args = p.parse_args()
    doc_types = (
        {s.strip() for s in args.doc_types.split(",") if s.strip()}
        if args.doc_types
        else None
    )
    return run(days=args.days, company_slug=args.company_slug, doc_types=doc_types)


if __name__ == "__main__":
    sys.exit(main())
