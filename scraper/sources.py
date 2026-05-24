"""Multi-source orchestrator: fetch announcements from BSE + NSE per company,
dedupe by source_url, and yield a single merged stream."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from scraper.bse_client import Announcement
from scraper.bse_client import fetch_announcements as fetch_bse
from scraper.db import Company
from scraper.nse_client import fetch_announcements as fetch_nse

log = logging.getLogger(__name__)


def fetch_all_for(
    company: Company,
    *,
    since: Optional[datetime] = None,
) -> list[Announcement]:
    """Return BSE+NSE announcements for one company, deduplicated by source_url.

    BSE is the primary source (more reliable, complete). NSE is best-effort —
    we add only those whose source_url didn't already come from BSE.
    """
    seen: set[str] = set()
    merged: list[Announcement] = []

    if company.bse_code:
        try:
            for ann in fetch_bse(company.bse_code, since=since):
                if ann.source_url in seen:
                    continue
                seen.add(ann.source_url)
                merged.append(ann)
        except Exception as e:
            log.warning("BSE fetch failed for %s: %s", company.slug, e)

    if company.nse_symbol:
        try:
            for ann in fetch_nse(company.nse_symbol, since=since):
                if ann.source_url in seen:
                    continue
                seen.add(ann.source_url)
                merged.append(ann)
        except Exception as e:
            log.warning("NSE fetch failed for %s: %s", company.slug, e)

    return merged
