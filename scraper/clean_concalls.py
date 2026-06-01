"""One-shot cleanup: make `concall` = real transcript ONLY, and de-duplicate.

Two phases:

  Phase 1 — RELABEL concalls:
    Scan every filing carrying the `concall` label. If the body/title is NOT a
    real earnings-call transcript (see transcript_detect.looks_like_transcript),
    DEMOTE it off `concall` — drop the label and re-pick the primary from what
    remains (or 'other'). Real transcripts are left as-is (the retitle pass
    already normalised their titles). This shrinks the over-applied concall
    label (e.g. "Con. Call Updates" notices) down to true transcripts.

  Phase 2 — GLOBAL DE-DUP:
    Group ALL filings by (company_id, normalised-title, posted-date). When a
    group has >1 row, keep ONE (prefer a real transcript title, then the longest
    title, then the lowest/oldest id) and delete the rest.

SAFETY: dry-run by default — it only logs what it WOULD change. Pass --apply to
actually write/delete.

Usage:
    python -m scraper.clean_concalls            # dry-run (report only)
    python -m scraper.clean_concalls --apply    # relabel + dedup for real
    python -m scraper.clean_concalls --apply --no-dedup
    python -m scraper.clean_concalls --apply --no-download
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter, defaultdict

from scraper.classifier import _pick_primary
from scraper.config import get_config, setup_logging
from scraper.db import (
    delete_filing,
    fetch_concall_candidates,
    fetch_filings_page,
    get_cached_text,
    update_filing_label_title,
)
from scraper.pdf_extract import download_pdf, extract_pages
from scraper.transcript_detect import (
    TRANSCRIPT_TITLE_RE,
    looks_like_transcript,
)

log = logging.getLogger("scraper.clean_concalls")

_PAGE = 200
_NORM_RE = re.compile(r"[^a-z0-9]+")


def _body_for(source_url: str, allow_download: bool) -> str | None:
    text = get_cached_text(source_url)
    if text:
        return text
    if not allow_download:
        return None
    try:
        pages = extract_pages(download_pdf(source_url))
        if not pages:
            return None
        return "\n\n".join(p.text for p in pages if p.text)
    except Exception as e:  # noqa: BLE001 — best effort
        log.warning("download failed for %s: %s", source_url, e)
        return None


def _norm_title(title: str) -> str:
    t = (title or "").lower()
    # strip the "transcript — " prefix so a retitled dup matches its raw twin
    t = re.sub(r"^transcript\s*[-–—:]\s*", "", t)
    return _NORM_RE.sub(" ", t).strip()


def relabel_concalls(*, apply: bool, allow_download: bool) -> Counter:
    """Demote non-transcript concall rows off the concall label."""
    stats: Counter[str] = Counter()
    offset = 0
    while True:
        rows = fetch_concall_candidates(limit=_PAGE, offset=offset)
        if not rows:
            break
        for r in rows:
            stats["scanned"] += 1
            title = r.get("title") or ""
            if TRANSCRIPT_TITLE_RE.search(title):
                stats["kept_transcript"] += 1
                continue
            body = _body_for(r["source_url"], allow_download)
            if looks_like_transcript(title, body):
                stats["kept_transcript"] += 1
                continue
            # Not a transcript → demote off concall.
            labels = [l for l in (r.get("labels") or []) if l != "concall"] or ["other"]
            new_primary = _pick_primary(labels)
            stats["demoted"] += 1
            log.info(
                "%s demote id=%s %r concall→%s",
                "APPLY" if apply else "DRY",
                r["id"],
                title[:70],
                new_primary,
            )
            if apply:
                try:
                    update_filing_label_title(
                        filing_id=r["id"], title=title, label=new_primary, labels=labels
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("demote update failed id=%s: %s", r["id"], e)
                    stats["demote_failed"] += 1
        offset += _PAGE
    return stats


def _rank(row: dict) -> tuple:
    """Higher tuple = better keep candidate."""
    title = row.get("title") or ""
    is_transcript = 1 if TRANSCRIPT_TITLE_RE.search(title) else 0
    # prefer transcript, then longer (more descriptive) title, then OLDER id
    return (is_transcript, len(title))


def dedup_filings(*, apply: bool) -> Counter:
    """Delete duplicate filings: same company + normalised title + posted date."""
    stats: Counter[str] = Counter()
    groups: dict[tuple, list[dict]] = defaultdict(list)
    offset = 0
    while True:
        rows = fetch_filings_page(limit=_PAGE, offset=offset)
        if not rows:
            break
        for r in rows:
            stats["scanned"] += 1
            date = (r.get("posted_at") or "")[:10]
            key = (r.get("company_id"), _norm_title(r.get("title") or ""), date)
            groups[key].append(r)
        offset += _PAGE
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        # Keep the best; delete the rest.
        rows_sorted = sorted(rows, key=lambda r: (_rank(r), r.get("id") or ""), reverse=True)
        keep = rows_sorted[0]
        losers = rows_sorted[1:]
        stats["dup_groups"] += 1
        stats["dup_rows"] += len(losers)
        log.info(
            "%s dedup keep=%s drop=%d title=%r date=%s",
            "APPLY" if apply else "DRY",
            keep["id"],
            len(losers),
            (keep.get("title") or "")[:60],
            key[2],
        )
        if apply:
            for l in losers:
                try:
                    delete_filing(l["id"])
                except Exception as e:  # noqa: BLE001
                    log.warning("delete failed id=%s: %s", l["id"], e)
                    stats["delete_failed"] += 1
    return stats


def run(*, apply: bool, allow_download: bool, dedup: bool) -> int:
    setup_logging()
    get_config()  # validate env early
    log.info("clean_concalls start apply=%s dedup=%s download=%s", apply, dedup, allow_download)
    rstats = relabel_concalls(apply=apply, allow_download=allow_download)
    log.info("relabel %s", dict(rstats))
    if dedup:
        dstats = dedup_filings(apply=apply)
        log.info("dedup %s", dict(dstats))
    log.info("clean_concalls done (apply=%s). Re-run with --apply to commit." if not apply else "clean_concalls done (committed).")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="scraper.clean_concalls")
    p.add_argument("--apply", action="store_true", help="commit changes (default: dry-run)")
    p.add_argument("--no-download", action="store_true", help="cached text only")
    p.add_argument("--no-dedup", action="store_true", help="skip the de-dup phase")
    args = p.parse_args()
    return run(apply=args.apply, allow_download=not args.no_download, dedup=not args.no_dedup)


if __name__ == "__main__":
    sys.exit(main())
