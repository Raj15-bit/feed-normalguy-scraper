"""One-time historical backfill. Usage:

    python -m scraper.backfill --days 365 [--company-slug reliance]
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from scraper.bse_client import fetch_announcements
from scraper.config import get_config, setup_logging
from scraper.db import list_companies
from scraper.pipeline import process_announcement

log = logging.getLogger("scraper.backfill")


def run(days: int, company_slug: str | None = None) -> int:
    setup_logging()
    cfg = get_config()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    companies = list_companies(only_with_bse=True)
    if company_slug:
        companies = [c for c in companies if c.slug == company_slug]
    log.info(
        "backfill since=%s companies=%d max_per_run=%d",
        since.date().isoformat(),
        len(companies),
        cfg.max_filings_per_run,
    )
    statuses: Counter[str] = Counter()
    processed = 0
    for company in companies:
        if not company.bse_code:
            continue
        try:
            anns = fetch_announcements(company.bse_code, since=since)
        except Exception as e:
            log.exception("fetch failed for %s: %s", company.slug, e)
            continue
        log.info("company=%s anns=%d", company.slug, len(anns))
        for ann in anns:
            if processed >= cfg.max_filings_per_run:
                log.info("max_filings_per_run reached, stopping")
                break
            res = process_announcement(company=company, ann=ann)
            statuses[res.status] += 1
            processed += 1
        if processed >= cfg.max_filings_per_run:
            break
    log.info("backfill complete processed=%d %s", processed, dict(statuses))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="scraper.backfill")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--company-slug", type=str, default=None)
    args = p.parse_args()
    return run(days=args.days, company_slug=args.company_slug)


if __name__ == "__main__":
    sys.exit(main())
