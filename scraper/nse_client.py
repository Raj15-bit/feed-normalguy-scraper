"""NSE corporate-announcements client.

NSE's `api/corporate-announcements` endpoint blocks naive requests; it
requires a cookie jar primed from a real homepage GET plus a browser-y
User-Agent. We prime once per process and reuse across calls.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scraper.bse_client import Announcement, _parse_dt

log = logging.getLogger(__name__)

NSE_ANN_URL = "https://www.nseindia.com/api/corporate-announcements"

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

_session: Optional[httpx.Client] = None
_primed: bool = False


def _client() -> httpx.Client:
    global _session, _primed
    if _session is None:
        _session = httpx.Client(headers=_HEADERS, timeout=httpx.Timeout(20.0))
    if not _primed:
        # Prime cookie jar — NSE sets cookies on homepage and a couple of
        # warm-up pages. Without these the API returns 401/403 or empty.
        try:
            _session.get("https://www.nseindia.com/")
            _session.get(
                "https://www.nseindia.com/companies-listing/"
                "corporate-filings-announcements"
            )
            _primed = True
        except httpx.HTTPError as e:
            log.warning("NSE prime failed (will retry on next call): %s", e)
    return _session


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
def fetch_announcements(
    nse_symbol: str,
    *,
    since: Optional[datetime] = None,
) -> list[Announcement]:
    """Return NSE announcements for one symbol. Best-effort: NSE may rate-limit
    or block GitHub Actions runners; failures are logged and return []."""
    since = since or (datetime.now(timezone.utc) - timedelta(days=2))
    to_date = datetime.now(timezone.utc)
    params = {
        "index": "equities",
        "from_date": since.strftime("%d-%m-%Y"),
        "to_date": to_date.strftime("%d-%m-%Y"),
        "symbol": nse_symbol,
    }
    try:
        c = _client()
        r = c.get(NSE_ANN_URL, params=params)
        if r.status_code >= 400:
            log.warning("NSE returned %s for %s — skipping", r.status_code, nse_symbol)
            return []
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("NSE fetch failed for %s: %s", nse_symbol, e)
        return []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("data") or []
    else:
        rows = []
    if not isinstance(rows, list):
        rows = []
    out: list[Announcement] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ann = _to_announcement(nse_symbol, row)
        if ann and ann.posted_at >= since:
            out.append(ann)
    return out


def _to_announcement(nse_symbol: str, raw: dict[str, Any]) -> Optional[Announcement]:
    if not isinstance(raw, dict):
        return None
    title = raw.get("desc") or raw.get("subject") or raw.get("attchmntText")
    if not title:
        return None
    url = raw.get("attchmntFile") or raw.get("attachmentFile") or raw.get("pdfFile")
    if not url:
        return None
    if not str(url).startswith("http"):
        url = f"https://nsearchives.nseindia.com/{str(url).lstrip('/')}"
    posted = raw.get("an_dt") or raw.get("sort_date") or raw.get("attchmntFile_date")
    posted_at = _parse_dt(posted) if posted else datetime.now(timezone.utc)
    return Announcement(
        bse_code="",   # NSE rows don't carry BSE scrip; left blank
        title=str(title).strip(),
        posted_at=posted_at,
        source_url=str(url).strip(),
        bse_category=str(raw.get("smIndustry") or raw.get("industry") or "") or None,
        bse_subcategory=str(raw.get("attchmntText") or "") or None,
        source="nse",
    )
