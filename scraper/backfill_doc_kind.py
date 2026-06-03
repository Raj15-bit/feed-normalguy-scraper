"""Backfill the decide-once columns (migration 0014) on existing filings.

For every filing we read the body we already cached in filing_content_cache and
run the same deciders the live pipeline uses (scraper/doc_period.py) to set
doc_kind / fiscal_year / fiscal_quarter / period_source / is_transcript.

Idempotent + re-runnable:
  - Deterministic output for a given (title, body, url, date) → re-runs are safe.
  - By default rows that already have a doc_kind are skipped (`--force` redoes).
  - Batched paging; `--limit` caps writes; `--report` writes nothing.

When no body is cached we still classify from the title rules and infer the
period from the date — and we LOG it (no silent guessing; period_source records
'date_inferred').

Usage:
    python -m scraper.backfill_doc_kind --report
    python -m scraper.backfill_doc_kind --company-slug reliance
    python -m scraper.backfill_doc_kind --apply            # whole universe
    python -m scraper.backfill_doc_kind --apply --force --company-slug reliance
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from scraper.config import get_config, setup_logging
from scraper.db import (
    fetch_filings_page,
    get_cached_text,
    list_companies,
    update_filing_doc_period,
)
from scraper.doc_period import classify_doc, decide_period

log = logging.getLogger("scraper.backfill_doc_kind")

_PAGE = 500


def run(
    *,
    apply: bool,
    company_slug: str | None,
    force: bool,
    max_rows: int | None,
) -> int:
    setup_logging()
    get_config()

    company_id: str | None = None
    if company_slug:
        match = [c for c in list_companies(only_with_bse=False) if c.slug == company_slug]
        if not match:
            raise SystemExit(f"no company with slug={company_slug!r}")
        company_id = match[0].id

    stats: Counter[str] = Counter()
    offset = 0
    changed = 0
    while True:
        rows = fetch_filings_page(limit=_PAGE, offset=offset)
        if not rows:
            break
        for r in rows:
            if company_id and r.get("company_id") != company_id:
                continue
            stats["scanned"] += 1
            if r.get("doc_kind") and not force:
                stats["skipped_done"] += 1
                continue

            title = r.get("title") or ""
            label = r.get("label") or ""
            url = r.get("source_url") or ""
            body = get_cached_text(url)
            if not body:
                stats["no_body"] += 1

            doc_kind, is_tx = classify_doc(title, body, label)
            fy, q, period_source = decide_period(
                title=title,
                body=body,
                posted_at=r.get("posted_at"),
                source_url=url,
                doc_kind=doc_kind,
            )
            stats[f"kind:{doc_kind}"] += 1
            stats[f"src:{period_source}"] += 1
            log.info(
                "id=%s kind=%s fy=%s q=%s src=%s tx=%s%s title=%r",
                r.get("id"),
                doc_kind,
                fy,
                q,
                period_source,
                is_tx,
                " NO_BODY" if not body else "",
                title[:80],
            )
            if apply:
                update_filing_doc_period(
                    filing_id=r["id"],
                    doc_kind=doc_kind,
                    fiscal_year=fy,
                    fiscal_quarter=q,
                    period_source=period_source,
                    is_transcript=is_tx,
                )
                stats["written"] += 1
            changed += 1
            if max_rows and changed >= max_rows:
                log.info("max reached (%d)", max_rows)
                log.info(
                    "backfill_doc_kind %s %s",
                    "APPLY" if apply else "REPORT",
                    dict(stats),
                )
                return 0
        offset += _PAGE
    log.info(
        "backfill_doc_kind %s %s", "APPLY" if apply else "REPORT", dict(stats)
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="scraper.backfill_doc_kind")
    p.add_argument("--apply", action="store_true", help="write (default: report)")
    p.add_argument("--company-slug", type=str, default=None)
    p.add_argument(
        "--force",
        action="store_true",
        help="recompute rows that already have a doc_kind",
    )
    p.add_argument("--limit", dest="max_rows", type=int, default=None)
    args = p.parse_args()
    return run(
        apply=args.apply,
        company_slug=args.company_slug,
        force=args.force,
        max_rows=args.max_rows,
    )


if __name__ == "__main__":
    sys.exit(main())
