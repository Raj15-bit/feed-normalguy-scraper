"""Backfill embeddings for filing_chunks that have a NULL embedding.

~40% of chunks (all OLDER ones) were inserted before the keyless fastembed
embedder, or had a transient embed failure, so their `embedding` is NULL. Vector
search (meaning-based chat retrieval) can't see them — only the latest docs are
covered. This re-embeds the null chunks with the SAME model the live embedder
uses (fastembed BAAI/bge-small-en-v1.5, 384-dim), so the vectors stay comparable.

Idempotent + resumable: it only touches rows that are still NULL and walks them
with a keyset cursor (id > after_id), so skipped/empty rows don't loop and a
re-run picks up wherever it left off.

Usage:
    python -m scraper.backfill_embeddings --report
    python -m scraper.backfill_embeddings --apply --company-slug reliance
    python -m scraper.backfill_embeddings --apply            # whole universe
    python -m scraper.backfill_embeddings --apply --limit 5000
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from scraper.config import get_config, setup_logging
from scraper.db import (
    fetch_null_embedding_chunks,
    list_companies,
    update_chunk_embedding,
)
from scraper.embedder import embed_all

log = logging.getLogger("scraper.backfill_embeddings")

_BATCH = 256


def run(
    *, apply: bool, company_slug: str | None, max_rows: int | None
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
    after_id: str | None = None
    done = 0
    while True:
        rows = fetch_null_embedding_chunks(
            limit=_BATCH, after_id=after_id, company_id=company_id
        )
        if not rows:
            break
        after_id = rows[-1]["id"]  # advance cursor regardless of skips
        texts = [(r.get("text") or "") for r in rows]
        # Empty-text chunks can't be embedded — skip (cursor already advanced).
        embed_idx = [i for i, t in enumerate(texts) if t.strip()]
        stats["scanned"] += len(rows)
        stats["empty"] += len(rows) - len(embed_idx)
        if not embed_idx:
            continue

        vecs = embed_all([texts[i] for i in embed_idx])
        if all(v is None for v in vecs):
            log.error("embedder returned all-NULL — model unavailable; aborting")
            log.info("backfill_embeddings %s %s", "APPLY" if apply else "REPORT", dict(stats))
            return 1

        for j, i in enumerate(embed_idx):
            emb = vecs[j]
            if emb is None:
                stats["embed_failed"] += 1
                continue
            if apply:
                update_chunk_embedding(chunk_id=rows[i]["id"], embedding=emb)
                stats["written"] += 1
            else:
                stats["would_write"] += 1
            done += 1
            if max_rows and done >= max_rows:
                log.info("max reached (%d)", max_rows)
                log.info(
                    "backfill_embeddings %s %s",
                    "APPLY" if apply else "REPORT",
                    dict(stats),
                )
                return 0
        log.info("progress: %s (cursor=%s)", dict(stats), after_id)

    log.info("backfill_embeddings %s %s", "APPLY" if apply else "REPORT", dict(stats))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="scraper.backfill_embeddings")
    p.add_argument("--apply", action="store_true", help="write (default: report)")
    p.add_argument("--company-slug", type=str, default=None)
    p.add_argument("--limit", dest="max_rows", type=int, default=None)
    args = p.parse_args()
    return run(apply=args.apply, company_slug=args.company_slug, max_rows=args.max_rows)


if __name__ == "__main__":
    sys.exit(main())
