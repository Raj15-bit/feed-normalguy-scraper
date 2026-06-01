# Transcripts — the rule (READ BEFORE TOUCHING CONCALL LOGIC)

Raj's hard requirement: **the Concalls section must contain ONLY real
earnings-call transcripts.** Not audio/video recordings, not "Con. Call
Updates" intimations, not analyst-meet notices — those go to **Other**.
Transcripts are the most important document; never pad Concalls with anything else.

## Why this is hard

BSE/NSE file the actual transcript in two ways:
1. As an announcement titled **"Transcript of … earnings call"** (easy — title says it).
2. As a PDF **attached to a generic "Analysts/Institutional Investor Meet/Con.
   Call Updates" notice** — the title never says "Transcript", so a title-only
   check misses it. This is why companies like **Shriram Finance** showed an
   empty Concall tab even though the transcript was filed.

There is no exchange flag distinguishing the two. So we detect from **content**.

## The detection (single source of truth)

`scraper/transcript_detect.py`:
- `looks_like_transcript(title, body)` → True if:
  - the title matches `\btranscript\b`, **OR**
  - the extracted PDF body (first ~6000 chars) contains "transcript" near an
    earnings-call phrase, **OR**
  - the body has ≥2 verbatim-call structural markers (Moderator / Operator /
    "ladies and gentlemen" / "question-and-answer" / "the next question" /
    "from the line of"). Conservative so a results/PPT PDF is never mis-flagged.
- `normalize_transcript_title(title)` → ensures the stored title carries
  "Transcript" (prefixes `Transcript — ` when missing). Idempotent.

When a transcript is detected, we **force `label = concall`** and **store the
normalized title**, so the app's title-based Concall filter
(`lib/doc-kind.ts` → `isRealStrict('concall', …)` = `/\btranscript\b/i`)
surfaces it. App side needs no change — keep the title rule as the gate.

## Where it runs

- **Forward (every scrape):** `scraper/pipeline.py` calls `looks_like_transcript`
  after classification and re-titles + pins concall before insert. New filings
  are correct automatically.
- **Existing rows:** `python -m scraper.retitle_transcripts` scans every
  concall-labelled filing, reads `filing_content_cache` (or downloads), and
  re-titles the ones that are actually transcripts. Idempotent — safe to re-run.
- **Missing entirely (never scraped):** `python -m scraper.backfill --days 400
  --doc-types concall` fetches concall filings (incl. oddly-titled transcripts
  via `_TITLE_KEYWORDS`) for the last ~4 quarters across all companies.
- **Automated:** `.github/workflows/transcripts.yml` runs both (backfill +
  retitle) weekly and on demand (`workflow_dispatch`, input `days`, default 400).

## What "last 4 quarters" means

~400 days back from today (covers the last ~4 quarterly calls). The app's
Concall section also applies a ~400-day recency window (`DOC_SECTIONS.sinceDays`
in `lib/company-queries.ts`), so stale transcripts drop to Other.

## Do NOT

- Do NOT put audio/video recordings, "Con. Call Updates" notices, or
  analyst-meet intimations in Concalls — they belong in Other.
- Do NOT relax the app's `\btranscript\b` title gate; instead fix detection here
  (re-title the row) so the gate keeps working.
- Do NOT widen `looks_like_transcript` so loosely that results/PPTs match.
