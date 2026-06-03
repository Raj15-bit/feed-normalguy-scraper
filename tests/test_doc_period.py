"""Unit tests for the decide-once deciders (scraper/doc_period.py).

Pure functions, no DB — runnable as `python -m tests.test_doc_period` or pytest.
Cases mirror real Reliance Industries filings (the mission's test company).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scraper.doc_period import (
    classify_doc,
    decide_period,
    quarter_from_date,
)

_IST = timezone(timedelta(hours=5, minutes=30))


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_classify_transcript_from_title():
    kind, tx = classify_doc(
        "Transcript of the discussion on the Unaudited Financial Results", "", "concall"
    )
    _check(kind == "transcript" and tx, f"title transcript -> {kind},{tx}")


def test_classify_transcript_from_body():
    body = (
        "Moderator: Ladies and gentlemen, good day and welcome to the Q1 FY26 "
        "earnings conference call. The next question is from the line of ..."
    )
    kind, tx = classify_doc("Con. Call Updates", body, "concall")
    _check(kind == "transcript" and tx, f"body transcript -> {kind},{tx}")


def test_classify_audio_not_transcript():
    kind, tx = classify_doc(
        "Audio recording of the earnings call", "", "concall"
    )
    _check(kind == "concall_audio" and not tx, f"audio -> {kind},{tx}")


def test_classify_notice_demoted():
    # An analyst-meet notice (no transcript, no audio) is a notice, not a concall.
    kind, tx = classify_doc(
        "Intimation of Analysts/Institutional Investor Meet to be held on ...",
        "We wish to inform that a meeting is scheduled.",
        "other",
    )
    _check(kind == "notice" and not tx, f"notice -> {kind},{tx}")


def test_classify_ppt_and_annual_and_rating():
    k1, _ = classify_doc("Investor Presentation Q1 FY26", "", "investor_ppt")
    _check(k1 == "investor_ppt", f"ppt -> {k1}")
    k2, _ = classify_doc("Integrated Annual Report 2024-25", "", "annual_report")
    _check(k2 == "annual_report", f"annual -> {k2}")
    k3, _ = classify_doc("CRISIL reaffirms rating", "", "credit_rating")
    _check(k3 == "credit_rating", f"rating -> {k3}")


def test_period_from_title():
    fy, q, src = decide_period(
        title="Q3 FY26 Earnings Call Transcript",
        body="",
        posted_at=datetime(2026, 1, 20, tzinfo=_IST),
        source_url="",
        doc_kind="transcript",
    )
    _check((fy, q, src) == (2026, 3, "title"), f"title period -> {fy},{q},{src}")


def test_period_from_body_quarter_ended():
    fy, q, src = decide_period(
        title="Con. Call Updates",
        body="Results for the quarter ended June 30, 2025 were discussed.",
        posted_at=datetime(2025, 8, 1, tzinfo=_IST),
        source_url="",
        doc_kind="transcript",
    )
    _check((fy, q, src) == (2026, 1, "body"), f"body period -> {fy},{q},{src}")


def test_period_date_inferred_report_lag():
    # Posted Aug 2025, no title/body period -> Q1 FY26 (report-lag mapping).
    fy, q, src = decide_period(
        title="Earnings Call Transcript",
        body="thanks everyone",
        posted_at=datetime(2025, 8, 5, tzinfo=_IST),
        source_url="",
        doc_kind="transcript",
    )
    _check((fy, q, src) == (2026, 1, "date_inferred"), f"date period -> {fy},{q},{src}")


def test_period_prefers_url_date_over_posted_at():
    # Posted_at collapsed onto scrape date 2026-06-02; URL says 15 Jul 2025 -> Q1 FY26.
    fy, q, src = decide_period(
        title="Updates",
        body="",
        posted_at=datetime(2026, 6, 2, tzinfo=_IST),
        source_url="https://nsearchives.nseindia.com/corporate/RELIANCE_15072025.pdf",
        doc_kind="transcript",
    )
    _check((fy, q, src) == (2026, 1, "date_inferred"), f"url date -> {fy},{q},{src}")


def test_annual_report_has_fy_no_quarter():
    fy, q, src = decide_period(
        title="Integrated Annual Report 2024-25",
        body="",
        posted_at=datetime(2025, 7, 1, tzinfo=_IST),
        source_url="",
        doc_kind="annual_report",
    )
    _check((fy, q, src) == (2025, None, "title"), f"annual period -> {fy},{q},{src}")


def test_quarter_from_date_boundaries():
    _check(quarter_from_date(datetime(2025, 4, 15, tzinfo=_IST)) == (2025, 4), "Apr->Q4")
    _check(quarter_from_date(datetime(2025, 7, 15, tzinfo=_IST)) == (2026, 1), "Jul->Q1")
    _check(quarter_from_date(datetime(2025, 10, 15, tzinfo=_IST)) == (2026, 2), "Oct->Q2")
    _check(quarter_from_date(datetime(2025, 12, 15, tzinfo=_IST)) == (2026, 3), "Dec->Q3")
    _check(quarter_from_date(datetime(2026, 2, 15, tzinfo=_IST)) == (2026, 3), "Feb->Q3")


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(_run_all())
