"""One-time historical backfill. Usage:

    python -m scraper.backfill --days 365 [--company-slug reliance]
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from scraper.config import get_config, setup_logging
from scraper.db import list_companies
from scraper.pipeline import process_announcement
from scraper.sources import fetch_all_for

log = logging.getLogger("scraper.backfill")


def run(days: int, company_slug: str | None = None) -> int:
    setup_logging()
    cfg = get_config()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    companies = list_companies(only_with_bse=False)
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
        if not (company.bse_code or company.nse_symbol):
            continue
        anns = fetch_all_for(company, since=since)
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
    args = p.parse_args()
    return run(days=args.days, company_slug=args.company_slug)


if __name__ == "__main__":
    sys.exit(main())
