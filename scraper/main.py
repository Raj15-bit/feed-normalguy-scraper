"""Cron entrypoint — fetches recent filings for all tracked companies."""
from __future__ import annotations

import logging
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from scraper.config import get_config, setup_logging
from scraper.db import list_companies
from scraper.pipeline import process_announcement
from scraper.sources import fetch_all_for

log = logging.getLogger("scraper.main")


def run(lookback_hours: int = 24) -> int:
    setup_logging()
    cfg = get_config()
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    companies = list_companies(only_with_bse=False)
    log.info(
        "starting cron lookback=%dh companies=%d max_per_run=%d",
        lookback_hours,
        len(companies),
        cfg.max_filings_per_run,
    )
    statuses: Counter[str] = Counter()
    processed = 0
    for company in companies:
        if processed >= cfg.max_filings_per_run:
            log.info("max_filings_per_run reached, stopping early")
            break
        if not (company.bse_code or company.nse_symbol):
            continue
        anns = fetch_all_for(company, since=since)
        log.info("company=%s anns=%d", company.slug, len(anns))
        for ann in anns:
            if processed >= cfg.max_filings_per_run:
                break
            res = process_announcement(company=company, ann=ann)
            statuses[res.status] += 1
            processed += 1
    log.info("cron complete processed=%d %s", processed, dict(statuses))
    return 0


if __name__ == "__main__":
    sys.exit(run())
