"""Repair pass: fix filings whose posted_at collapsed onto the scrape date.

Root cause (fixed forward in bse_client._parse_dt): NSE sends `an_dt` like
"15-Jul-2025 18:30:00", which the old parser didn't recognise → it defaulted to
now(). Every NSE filing therefore landed on the scrape date, so quarter/month
labels were wrong and companies showed 0/4 transcripts.

Existing rows can't be fixed by a re-scrape (slug = md5(url) is idempotent, so
the row is skipped). But the TRUE date is encoded in the NSE attachment URL,
e.g. ".../Infosys_15072025xxxx.pdf" → 15 Jul 2025. This pass scans every
filing, re-derives the real date from the URL, and corrects posted_at in place.

Idempotent: only writes when the URL-derived date differs from the stored one
by more than 2 days. Usage:
    python -m scraper.fix_dates --report        # show what would change
    python -m scraper.fix_dates --apply         # write corrections
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from scraper.config import get_config, setup_logging

# NOTE: scraper.db is imported lazily inside run() (it pulls in the supabase
# client). Keeping it out of module scope lets pure helpers here — date_from_url,
# _quarter_label — be imported (e.g. by scraper.doc_period) without DB deps.

log = logging.getLogger("scraper.fix_dates")

_IST = timezone(timedelta(hours=5, minutes=30))

# NSE attachment filenames embed the filing date as SYMBOL_DDMMYYYY[idx].
# Anchor on an underscore so we don't catch digits inside the company name.
_URL_DATE_RE = re.compile(r"_(\d{2})(\d{2})(20\d{2})(?!\d{3,})")

# Generic, information-free titles we upgrade once we know the real quarter.
_VAGUE_RE = re.compile(
    r"^(transcript\s*[-—:]\s*)?(updates?|general updates?|announcements?|"
    r"company update|disclosures?)\s*$",
    re.I,
)
_IS_TRANSCRIPT_RE = re.compile(r"\btranscript\b", re.I)

_PAGE = 500


def _quarter_label(when: datetime) -> str:
    """Reported fiscal quarter from a posting date — MUST stay in lock-step
    with coverage._quarter_label / app lib/fy.ts (report-lag mapping)."""
    d = when.astimezone(_IST)
    mo, y = d.month, d.year
    if mo in (4, 5):
        fy, q = y, 4
    elif mo in (6, 7, 8):
        fy, q = y + 1, 1
    elif mo in (9, 10, 11):
        fy, q = y + 1, 2
    elif mo == 12:
        fy, q = y + 1, 3
    else:  # Jan, Feb, Mar
        fy, q = y, 3
    return f"Q{q} FY{fy % 100}"


def date_from_url(url: str) -> datetime | None:
    """Extract the true filing date from an NSE attachment URL, or None."""
    if not url:
        return None
    m = _URL_DATE_RE.search(url)
    if not m:
        return None
    dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= dd <= 31 and 1 <= mm <= 12 and 2015 <= yyyy <= 2030):
        return None
    try:
        # Noon IST — safely inside the day, away from midnight quarter edges.
        return datetime(yyyy, mm, dd, 12, 0, tzinfo=_IST)
    except ValueError:
        return None


def _better_title(title: str, label: str, when: datetime) -> str | None:
    """Upgrade a vague concall title to name its quarter, once we know the
    real date. Returns None when no change is warranted."""
    if label != "concall":
        return None
    if not _VAGUE_RE.match((title or "").strip()):
        return None
    q = _quarter_label(when)
    return f"Transcript — {q} Earnings Call"


def run(*, apply: bool, max_rows: int | None = None) -> int:
    from scraper.db import fetch_filings_page, update_filing_posted_at

    setup_logging()
    get_config()
    stats: Counter[str] = Counter()
    offset = 0
    changed = 0
    while True:
        rows = fetch_filings_page(limit=_PAGE, offset=offset)
        if not rows:
            break
        for r in rows:
            stats["scanned"] += 1
            url = r.get("source_url") or ""
            real = date_from_url(url)
            if real is None:
                continue
            stats["url_dated"] += 1
            try:
                cur = datetime.fromisoformat(
                    str(r["posted_at"]).replace("Z", "+00:00")
                )
            except (ValueError, KeyError):
                cur = None
            # Only correct when the stored date is materially off.
            if cur is not None and abs((cur - real).days) <= 2:
                continue
            label = r.get("label") or ""
            title = r.get("title") or ""
            new_title = _better_title(title, label, real)
            stats["would_fix"] += 1
            log.info(
                "fix id=%s %s -> %s%s",
                r.get("id"),
                cur.date().isoformat() if cur else "?",
                real.date().isoformat(),
                f"  title={new_title!r}" if new_title else "",
            )
            if apply:
                update_filing_posted_at(
                    filing_id=r["id"],
                    posted_at=real.isoformat(),
                    title=new_title,
                )
                stats["fixed"] += 1
            changed += 1
            if max_rows and changed >= max_rows:
                log.info("max reached (%d)", max_rows)
                log.info("fix_dates %s %s", "APPLY" if apply else "REPORT", dict(stats))
                return 0
        offset += _PAGE
    log.info("fix_dates %s %s", "APPLY" if apply else "REPORT", dict(stats))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="scraper.fix_dates")
    p.add_argument("--apply", action="store_true", help="write fixes (default: report)")
    p.add_argument("--max", type=int, default=None, help="cap rows changed")
    args = p.parse_args()
    return run(apply=args.apply, max_rows=args.max)


if __name__ == "__main__":
    sys.exit(main())
