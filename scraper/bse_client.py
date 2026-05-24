"""Thin wrapper around Benny Thadikaran's `bse` package.

The bse package is unofficial; its API shape varies slightly between releases.
We isolate everything through this module so a future swap (e.g. NSE) requires
changes in only one place.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from bse import BSE
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)


@dataclass
class Announcement:
    bse_code: str
    title: str           # Either headline or news subject
    posted_at: datetime  # UTC
    source_url: str      # PDF URL on BSE
    bse_category: Optional[str]
    bse_subcategory: Optional[str]


_bse: Optional[BSE] = None


def _client() -> BSE:
    global _bse
    if _bse is None:
        # `BSE()` requires a dir for caching; use system tmp to avoid clutter.
        import tempfile

        _bse = BSE(tempfile.gettempdir())
    return _bse


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def fetch_announcements(
    bse_code: str,
    *,
    since: Optional[datetime] = None,
) -> list[Announcement]:
    """Return announcements for a single scrip, newest-first.

    The `bse` package's `announcements()` returns up to a couple hundred recent
    items. We filter by `since` client-side.
    """
    since = since or (datetime.now(timezone.utc) - timedelta(days=2))
    try:
        # bse 1.x: BSE.announcements(scripcode, segment="equity", from_date=..., to_date=...)
        raw = _client().announcements(scripcode=bse_code, segment="equity")
    except TypeError:
        # Older bse versions accepted only positional args.
        raw = _client().announcements(bse_code)
    return [
        ann
        for ann in (_to_announcement(bse_code, item) for item in raw or [])
        if ann is not None and ann.posted_at >= since
    ]


def _to_announcement(bse_code: str, raw: dict[str, Any]) -> Optional[Announcement]:
    # Field names vary by bse version. We probe common spellings.
    title = (
        raw.get("HEADLINE")
        or raw.get("NEWSSUB")
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
        or raw.get("attachmentName")
    )
    if not url:
        return None
    if not url.startswith("http"):
        url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{url}"
    posted = (
        raw.get("NEWS_DT")
        or raw.get("DT_TM")
        or raw.get("news_dt")
        or raw.get("posted_at")
    )
    if isinstance(posted, str):
        posted_at = _parse_dt(posted)
    elif isinstance(posted, datetime):
        posted_at = posted if posted.tzinfo else posted.replace(tzinfo=timezone.utc)
    else:
        posted_at = datetime.now(timezone.utc)
    return Announcement(
        bse_code=bse_code,
        title=str(title).strip(),
        posted_at=posted_at,
        source_url=str(url).strip(),
        bse_category=str(raw.get("CATEGORYNAME") or raw.get("category") or "") or None,
        bse_subcategory=str(raw.get("SUBCATNAME") or raw.get("subcategory") or "") or None,
    )


def _parse_dt(value: str) -> datetime:
    # BSE often returns "2026-01-15T11:32:00" or "2026-01-15 11:32:00.0"
    value = value.replace("Z", "+00:00").strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value[:26] if "." in value else value, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        log.warning("could not parse datetime %r — defaulting to now()", value)
        return datetime.now(timezone.utc)


def iter_announcements_for_companies(
    bse_codes: Iterable[str],
    *,
    since: Optional[datetime] = None,
) -> Iterable[Announcement]:
    for code in bse_codes:
        try:
            yield from fetch_announcements(code, since=since)
        except Exception as e:
            log.exception("fetch_announcements failed for %s: %s", code, e)
            continue
