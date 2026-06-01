"""Direct BSE corporate-announcements JSON client (no third-party lib).

BSE exposes `api.bseindia.com/BseIndiaAPI/api/AnnGetData/w` which returns
a JSON envelope `{"Table": [...]}`. We hit it per-scrip with a sliding
date window and map each row into our internal Announcement dataclass.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

BSE_ANN_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"

_DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


@dataclass
class Announcement:
    bse_code: str
    title: str
    posted_at: datetime
    source_url: str
    bse_category: Optional[str]
    bse_subcategory: Optional[str]
    source: str = "bse"  # 'bse' or 'nse'


def _client() -> httpx.Client:
    return httpx.Client(headers=_DEFAULT_HEADERS, timeout=httpx.Timeout(20.0))


# BSE truncates a single AnnGetData response, so a long backfill window must be
# fetched in smaller sub-windows and merged. ~45 days keeps each response small.
_CHUNK_DAYS = 45


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
def _fetch_window(
    bse_code: str, win_from: datetime, win_to: datetime
) -> list[Announcement]:
    """One BSE call for a single [win_from, win_to] date window."""
    params = {
        "strCat": "-1",
        "strPrevDate": win_from.strftime("%Y%m%d"),
        "strScrip": bse_code,
        "strSearch": "P",
        "strToDate": win_to.strftime("%Y%m%d"),
        "strType": "C",
    }
    with _client() as c:
        r = c.get(BSE_ANN_URL, params=params)
        r.raise_for_status()
        data = r.json()
    rows = data.get("Table") or data.get("data") or []
    out: list[Announcement] = []
    for row in rows:
        ann = _to_announcement(bse_code, row)
        if ann:
            out.append(ann)
    return out


def fetch_announcements(
    bse_code: str,
    *,
    since: Optional[datetime] = None,
) -> list[Announcement]:
    """Return BSE announcements for one scrip since `since`, fetched in
    ~45-day windows (so BSE can't silently truncate a long backfill) and
    de-duplicated by source_url."""
    since = since or (datetime.now(timezone.utc) - timedelta(days=2))
    to_date = datetime.now(timezone.utc)

    seen: set[str] = set()
    out: list[Announcement] = []
    win_from = since
    while win_from < to_date:
        win_to = min(win_from + timedelta(days=_CHUNK_DAYS), to_date)
        try:
            for ann in _fetch_window(bse_code, win_from, win_to):
                if ann.posted_at < since:
                    continue
                if ann.source_url in seen:
                    continue
                seen.add(ann.source_url)
                out.append(ann)
        except Exception as e:  # one bad window shouldn't kill the whole range
            log.warning(
                "BSE window %s..%s failed for %s: %s",
                win_from.date(),
                win_to.date(),
                bse_code,
                e,
            )
        win_from = win_to
    return out


def _to_announcement(bse_code: str, raw: dict[str, Any]) -> Optional[Announcement]:
    title = (
        raw.get("HEADLINE")
        or raw.get("NEWSSUB")
        or raw.get("NEWS_SUB")
        or raw.get("headline")
        or raw.get("news_subject")
        or raw.get("subject")
    )
    if not title:
        return None
    url = (
        raw.get("ATTACHMENTNAME")
        or raw.get("attachment")
        or raw.get("pdf_link")
    )
    if not url:
        return None
    if not str(url).startswith("http"):
        url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{url}"
    posted = (
        raw.get("NEWS_DT")
        or raw.get("DT_TM")
        or raw.get("news_dt")
        or raw.get("posted_at")
    )
    posted_at = _parse_dt(posted) if posted else datetime.now(timezone.utc)
    return Announcement(
        bse_code=bse_code,
        title=str(title).strip(),
        posted_at=posted_at,
        source_url=str(url).strip(),
        bse_category=str(raw.get("CATEGORYNAME") or raw.get("category") or "") or None,
        bse_subcategory=str(raw.get("SUBCATNAME") or raw.get("subcategory") or "") or None,
        source="bse",
    )


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).replace("Z", "+00:00").strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s[:26] if "." in s else s, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        log.warning("could not parse datetime %r — defaulting to now()", value)
        return datetime.now(timezone.utc)
