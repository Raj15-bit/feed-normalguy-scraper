"""Self-maintaining coverage / missing-item engine.

For every Nifty-50 company it builds a (doc-type × FY2026 quarter) matrix of the
REAL documents present, classifies each empty/bad cell into one of THREE
missing-item levels, and (with --apply) fills the gaps via targeted, idempotent
backfills + a 6-month announcements top-up.

Missing-item levels (mirrors docs/COVERAGE-AND-SELFHEAL.md):
  L1 absent      — no real-doc row for (company, type, quarter)
  L2 broken      — a row exists but its content cache is missing/empty (dead link)
  L3 mislabelled — a row carries the type label but isn't the real doc (fails strict)

Idempotent: backfill dedups by slug=md5(url) + filing_exists, so re-runs add no
clones. Split into small rotating slices for the 6-hourly workflow.

Usage:
    python -m scraper.coverage --report                 # whole universe, no writes
    python -m scraper.coverage --report --company-slug reliance
    python -m scraper.coverage --apply                  # fill all gaps
    python -m scraper.coverage --apply --slice auto      # 1/N rotating slice (cron)
    python -m scraper.coverage --apply --slice 1/4
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

# Indian Standard Time (UTC+5:30) — filings post in IST; read date parts in IST
# so quarter/FY match the app (lib/fy.ts) near midnight boundaries.
_IST = timezone(timedelta(hours=5, minutes=30))

from scraper.backfill import run as backfill_run
from scraper.config import get_config, setup_logging
from scraper.db import (
    cache_is_healthy,
    fetch_company_docrows,
    list_companies,
)

log = logging.getLogger("scraper.coverage")

FY_START_ISO = "2025-03-01"  # FY2026 = Mar 2025 → Mar 2026
TARGET_QUARTERS = ["Q1 FY26", "Q2 FY26", "Q3 FY26", "Q4 FY26"]
DOC_TYPES = ["concall", "investor_ppt", "credit_rating", "annual_report"]

# Strict real-doc title gates — mirror app lib/doc-kind.ts STRICT_RE.
_STRICT = {
    "concall": re.compile(r"\btranscript\b", re.I),
    "investor_ppt": re.compile(r"\bpresentation\b", re.I),
    "credit_rating": re.compile(
        r"\b(rating|rated|crisil|icra|care|moody|fitch|s&p|s & p|india ratings|ind-?ra)\b",
        re.I,
    ),
    "annual_report": re.compile(r"\bannual report\b", re.I),
}

_TITLE_Q = re.compile(r"\bq([1-4])\s*fy\s*'?\s*(\d{2,4})\b", re.I)


def _is_real(label: str, title: str) -> bool:
    pat = _STRICT.get(label)
    return bool(pat and pat.search(title or ""))


def _quarter_label(title: str, iso: str) -> str:
    m = _TITLE_Q.search(title or "")
    if m:
        q = int(m.group(1))
        fy = int(m.group(2))
        if fy < 100:
            fy += 2000
        return f"Q{q} FY{fy % 100}"
    # Map POSTED month → the fiscal quarter it REPORTS (calls land ~3-6 weeks
    # after quarter-end). Python month is 1-indexed (Jan=1..Dec=12).
    # MUST stay IN LOCK-STEP with app lib/fy.ts `fyQuarterFromDate`.
    #   Apr-May → Q4 (FY ending this Mar) · Jun-Aug → Q1 (new FY) ·
    #   Sep-Nov → Q2 · Dec → Q3 (new FY) · Jan-Mar → Q3 (FY ending this Mar).
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_IST)
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


def company_gaps(company) -> dict:
    """Per-company coverage cells + the list of missing (type, quarter, reason)."""
    rows = fetch_company_docrows(
        company_id=company.id, labels=DOC_TYPES, since_iso=FY_START_ISO
    )
    # present[type][quarter] = list of real-doc source_urls (broken-link check
    # considers a cell healthy if ANY copy is fetchable).
    present: dict[str, dict[str, list[str]]] = {t: {} for t in DOC_TYPES}
    mislabelled = 0
    for r in rows:
        label = r.get("label") or ""
        title = r.get("title") or ""
        if label not in DOC_TYPES:
            continue
        if not _is_real(label, title):
            mislabelled += 1  # L3: labelled the type but not the real doc
            continue
        q = "FY26" if label == "annual_report" else _quarter_label(title, r["posted_at"])
        present[label].setdefault(q, []).append(r.get("source_url") or "")

    missing: list[dict] = []
    for t in ("concall", "investor_ppt", "credit_rating"):
        for q in TARGET_QUARTERS:
            urls = present[t].get(q) or []
            if not urls:
                missing.append({"type": t, "quarter": q, "reason": "absent"})
            elif not any(cache_is_healthy(u) for u in urls if u):
                missing.append({"type": t, "quarter": q, "reason": "broken"})
    if not present["annual_report"]:
        missing.append({"type": "annual_report", "quarter": "FY26", "reason": "absent"})

    return {
        "slug": company.slug,
        "present": {t: sorted(present[t].keys()) for t in DOC_TYPES},
        "mislabelled": mislabelled,
        "missing": missing,
    }


def _select_slice(companies: list, spec: str | None) -> list:
    if not spec:
        return companies
    n_slices = 4  # ~13 companies/run → all 50 covered each day at 6h cadence
    if spec == "auto":
        idx = (datetime.now(timezone.utc).hour // 6) % n_slices
    else:
        parts = spec.split("/")
        if len(parts) != 2:
            raise SystemExit(f"--slice must be 'auto' or 'i/N', got {spec!r}")
        try:
            i, n_slices = int(parts[0]), int(parts[1])
        except ValueError:
            raise SystemExit(f"--slice i/N must be integers, got {spec!r}")
        if n_slices < 1 or not (1 <= i <= n_slices):
            raise SystemExit(f"--slice out of range: need 1<=i<=N, got {spec!r}")
        idx = i - 1
    out = [c for i, c in enumerate(companies) if i % n_slices == idx]
    log.info("slice %s → %d/%d companies", spec, len(out), len(companies))
    return out


def run(*, apply: bool, company_slug: str | None, slice_spec: str | None) -> int:
    setup_logging()
    get_config()
    companies = list_companies(only_with_bse=False)
    if company_slug:
        companies = [c for c in companies if c.slug == company_slug]
    else:
        companies = _select_slice(companies, slice_spec)

    stats: Counter[str] = Counter()
    for c in companies:
        if not (c.bse_code or c.nse_symbol):
            continue
        g = company_gaps(c)
        stats["companies"] += 1
        stats["gaps"] += len(g["missing"])
        if g["missing"]:
            types = sorted({m["type"] for m in g["missing"]})
            log.info(
                "%s gaps=%d types=%s mislabelled=%d",
                c.slug,
                len(g["missing"]),
                types,
                g["mislabelled"],
            )
            if apply:
                # Targeted, idempotent backfill of just the missing doc types,
                # over the full FY window (covers all 4 quarters).
                backfill_run(days=400, company_slug=c.slug, doc_types=set(types))
                # 6-month announcements top-up (everything else, capped per run).
                backfill_run(days=180, company_slug=c.slug, doc_types=None)
                stats["filled_companies"] += 1
        else:
            log.info("%s OK (full FY2026 coverage)", c.slug)
    log.info("coverage %s %s", "APPLY" if apply else "REPORT", dict(stats))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="scraper.coverage")
    p.add_argument("--apply", action="store_true", help="fill gaps (default: report)")
    p.add_argument("--company-slug", type=str, default=None)
    p.add_argument("--slice", dest="slice_spec", type=str, default=None, help="auto | i/N")
    args = p.parse_args()
    return run(apply=args.apply, company_slug=args.company_slug, slice_spec=args.slice_spec)


if __name__ == "__main__":
    sys.exit(main())
