"""Decide a filing's document TYPE and fiscal PERIOD from its PDF body — once,
at scrape time — so the app never has to re-guess them from the title + date.

Two pure, unit-testable entry points:

  classify_doc(title, body, label) -> (doc_kind, is_transcript)
      doc_kind ∈ {transcript, concall_audio, investor_ppt, annual_report,
                  credit_rating, notice, other}

  decide_period(title, body, posted_at, source_url, doc_kind)
      -> (fiscal_year:int|None, fiscal_quarter:int|None, period_source:str)
      period_source ∈ {title, body, date_inferred}

Precedence for the period is TITLE → BODY → DATE_INFERRED, and a date-inferred
period is ALWAYS flagged via period_source so nothing is silently guessed. The
date-inference mapping is kept IN LOCK-STEP with scraper/coverage.py,
scraper/fix_dates.py and the app's lib/fy.ts (report-lag: calls land weeks after
quarter-end).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from scraper.fix_dates import date_from_url
from scraper.transcript_detect import (
    is_intimation_title,
    looks_like_transcript,
)

_IST = timezone(timedelta(hours=5, minutes=30))

# How much of the body to inspect for type/period markers.
_HEAD_CHARS = 8000

# ── Document-type markers (mirror the app's lib/doc-kind.ts REAL_RE) ──────────
# Audio recording of a call (a real concall doc, but NOT a transcript).
_AUDIO_RE = re.compile(
    r"\b(audio[\s-]*recording|audio file|earnings call recording|"
    r"concall recording|recording of (the )?(earnings|analyst|investor|"
    r"con(ference)?\.?\s*call))\b",
    re.I,
)
_PPT_RE = re.compile(
    r"\b(investor presentation|earnings presentation|analyst presentation|"
    r"results presentation|corporate presentation|presentation on|"
    r"q[1-4]\s*fy\s*\d+\s*presentation)\b",
    re.I,
)
_ANNUAL_RE = re.compile(r"\b(integrated annual report|annual report)\b", re.I)
# Mirror the app's credit_rating STRICT_RE.
_RATING_RE = re.compile(
    r"\b(rating|rated|crisil|icra|care|moody|fitch|s&p|s & p|"
    r"india ratings|ind-?ra|brickwork|acuit[eé])\b",
    re.I,
)


def classify_doc(
    title: str, body: Optional[str], label: Optional[str]
) -> tuple[str, bool]:
    """Decide (doc_kind, is_transcript) from the body + title (label = hint).

    Deterministic and conservative: transcript wins over everything, then audio,
    then PPT / annual / rating; a pre-event notice is `notice`; else `other`.
    The app only consults doc_kind to tell a REAL document from a notice WITHIN a
    label bucket, so a non-strict filing landing on `other` is harmless.
    """
    is_tx = looks_like_transcript(title or "", body)
    if is_tx:
        return "transcript", True

    text = ((title or "") + "\n" + (body or "")[:_HEAD_CHARS])

    if _AUDIO_RE.search(text):
        return "concall_audio", False
    if _PPT_RE.search(text):
        return "investor_ppt", False
    if _ANNUAL_RE.search(text):
        return "annual_report", False
    if _RATING_RE.search(text):
        return "credit_rating", False
    if is_intimation_title(title or ""):
        return "notice", False
    return "other", False


# ── Period extraction ─────────────────────────────────────────────────────────
# "Q3 FY26", "Q1 FY'25", "Q4 FY 2025" — same shape as coverage._TITLE_Q.
_TITLE_Q = re.compile(r"\bq([1-4])\s*fy\s*'?\s*(\d{2,4})\b", re.I)
# "for the quarter ended June 30, 2025" / "quarter and year ended 31 March 2025".
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_QUARTER_ENDED_RE = re.compile(
    r"quarter\s+(?:and\s+\w+\s+)?ended\s+"
    r"(?:(\d{1,2})\s+)?"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"(?:\s+(\d{1,2}))?[, ]+(\d{4})",
    re.I,
)
# Annual report FY: "2024-25", "2024-2025", or "FY25"/"FY 2025".
_FY_RANGE_RE = re.compile(r"\b(20\d{2})\s*[-–/]\s*(\d{4}|\d{2})\b")
_FY_RE = re.compile(r"\bfy\s*'?\s*(\d{4}|\d{2})\b", re.I)


def _norm_year(y: int) -> int:
    return y + 2000 if y < 100 else y


def quarter_from_date(dt: datetime) -> tuple[int, int]:
    """Reported fiscal (year, quarter) from a posting date — report-lag mapping,
    IN LOCK-STEP with coverage._quarter_label / fix_dates._quarter_label /
    app lib/fy.ts fyQuarterFromDate."""
    d = dt.astimezone(_IST)
    mo, y = d.month, d.year
    if mo in (4, 5):
        return y, 4
    if mo in (6, 7, 8):
        return y + 1, 1
    if mo in (9, 10, 11):
        return y + 1, 2
    if mo == 12:
        return y + 1, 3
    return y, 3  # Jan, Feb, Mar


def _quarter_from_period_end(month: int, cal_year: int) -> Optional[tuple[int, int]]:
    """Map a stated QUARTER-END (e.g. 'ended June 2025') to (fiscal_year, q).
    The end-month names the quarter directly: Jun→Q1, Sep→Q2, Dec→Q3, Mar→Q4."""
    if month == 6:
        return cal_year + 1, 1
    if month == 9:
        return cal_year + 1, 2
    if month == 12:
        return cal_year + 1, 3
    if month == 3:
        return cal_year, 4
    return None  # not a fiscal quarter-end month


def _period_from_title(title: str) -> Optional[tuple[int, int]]:
    m = _TITLE_Q.search(title or "")
    if not m:
        return None
    return _norm_year(int(m.group(2))), int(m.group(1))


def _period_from_body(body: Optional[str]) -> Optional[tuple[int, int]]:
    head = (body or "")[:_HEAD_CHARS]
    if not head:
        return None
    m = _TITLE_Q.search(head)
    if m:
        return _norm_year(int(m.group(2))), int(m.group(1))
    m = _QUARTER_ENDED_RE.search(head)
    if m:
        mon = _MONTHS[m.group(2).lower()[:3]]
        cal_year = int(m.group(4))
        return _quarter_from_period_end(mon, cal_year)
    return None


def _annual_fy_from_title(title: str) -> Optional[int]:
    """FY (ending year, 4-digit) named in an annual-report title, or None.
    'Annual Report 2024-25' → 2025; 'FY25' → 2025."""
    t = title or ""
    rng = _FY_RANGE_RE.search(t)
    if rng:
        return _norm_year(int(rng.group(2)))
    fy = _FY_RE.search(t)
    if fy:
        return _norm_year(int(fy.group(1)))
    return None


def _coerce_dt(posted_at) -> datetime:
    if isinstance(posted_at, datetime):
        return posted_at
    return datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))


def decide_period(
    *,
    title: str,
    body: Optional[str],
    posted_at,
    source_url: Optional[str],
    doc_kind: str,
) -> tuple[Optional[int], Optional[int], str]:
    """Return (fiscal_year, fiscal_quarter, period_source).

    Annual reports carry a fiscal_year but no quarter. For everything else the
    quarter is resolved title → body → date_inferred; the date fallback prefers
    the NSE-URL date (the true filing date) over a possibly-collapsed posted_at.
    """
    if doc_kind == "annual_report":
        fy = _annual_fy_from_title(title)
        if fy is not None:
            return fy, None, "title"
        dt = date_from_url(source_url or "") or _coerce_dt(posted_at)
        fy, _q = quarter_from_date(dt)
        return fy, None, "date_inferred"

    p = _period_from_title(title)
    if p:
        return p[0], p[1], "title"
    p = _period_from_body(body)
    if p:
        return p[0], p[1], "body"
    dt = date_from_url(source_url or "") or _coerce_dt(posted_at)
    fy, q = quarter_from_date(dt)
    return fy, q, "date_inferred"
