# feed-normalguy-scraper

Python scraper that ingests Indian corporate filings from BSE into the Supabase database powering [feed.normalguy.co.in](https://feed.normalguy.co.in).

This is the Python companion to the main [feed-normalguy](../feed%20normal%20guy) Next.js repo. Runs on GitHub Actions (free, unlimited minutes on public repos).

## What it does

```
BSE API → download PDF → pymupdf text extract → classify label (rules → DeepSeek)
       → chunk to ~800 tokens → OpenAI embed batch
       → INSERT filings + filing_chunks → queue_filing_alerts() RPC
```

Per filing cost: ~₹0.001 (classifier, when needed) + ~₹0.02 (embeddings). One year of Nifty 50 backfill: ~₹40 total.

## Setup

### 1. Apply the demo schema in the Next.js repo first

This scraper writes to tables defined by the Next.js repo's migrations:

- `feed normal guy/supabase/migrations/0000_base_schema_demo.sql` (creates the 9 tables, RLS, hybrid_search, handle_new_user trigger, queue_filing_alerts)
- `feed normal guy/supabase/migrations/0001_chat.sql` (quota RPCs)
- `feed normal guy/supabase/migrations/0002_watchlist_alerts.sql` (claim_alert_batch RPC)

Paste each into Supabase Dashboard → SQL Editor → Run.

### 2. Seed Nifty 50 companies

Paste `seeds/0001_nifty50.sql` (in this repo) into Supabase SQL Editor → Run.
That populates `companies` with 50 rows including the `bse_code` values the scraper needs.

### 3. Make a public GitHub repo

```bash
cd "F:/feed-normalguy-scraper"
git init -b main
git add .
git commit -m "init scraper"
gh repo create feed-normalguy-scraper --public --source=. --push
```

(Or create the repo via the GitHub UI and `git remote add origin … && git push -u origin main`.)

**Public repo matters**: GitHub Actions minutes are unlimited and free for public repos. Private repos get 2000 minutes/mo on the free tier.

### 4. Add GitHub Actions secrets

Repo → Settings → Secrets and variables → Actions → New repository secret. Add:

- `SUPABASE_URL` — e.g. `https://ymgeatujnvdbuainweqf.supabase.co`
- `SUPABASE_SERVICE_KEY` — Supabase Dashboard → Project Settings → API → `service_role` key. **NEVER paste this in chat, in client code, or in a public file.** Only here.
- `OPENAI_API_KEY` — https://platform.openai.com/api-keys
- `DEEPSEEK_API_KEY` — https://platform.deepseek.com → API Keys

### 5. Run the backfill once

Repo → Actions tab → **backfill** workflow → Run workflow → `days=365`. Takes 30-90 min depending on PDF download speeds and OpenAI throughput.

After it completes, `filings` and `filing_chunks` will be populated. Your Next.js app at `feed.normalguy.co.in` (or `localhost:3000`) shows real filings and the AI chat actually works.

### 6. The ongoing cron starts automatically

`scrape.yml` runs every 2 hours during IST market hours via `cron`. No further action needed.

## Local development

```bash
cd "F:/feed-normalguy-scraper"
py -m venv .venv
.venv\Scripts\activate         # Windows; use source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env
# Fill in SUPABASE_URL, SUPABASE_SERVICE_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY in .env

# Test against one company
python -m scraper.backfill --days 30 --company-slug reliance

# Cron-style run
python -m scraper.main
```

## Architecture

| File | Role |
|---|---|
| `scraper/config.py` | Loads + validates env, sets up logging |
| `scraper/labels.py` | Canonical 18 labels (mirrors `lib/labels.ts`) |
| `scraper/db.py` | Supabase service-role client + DAO functions |
| `scraper/bse_client.py` | Wraps the `bse` PyPI package |
| `scraper/pdf_extract.py` | Downloads PDF, extracts text with pymupdf |
| `scraper/classifier.py` | 3-layer: BSE subcategory map → title regex → DeepSeek |
| `scraper/chunker.py` | Splits per-page text into ~800-token chunks |
| `scraper/embedder.py` | Batched OpenAI text-embedding-3-small calls |
| `scraper/pipeline.py` | Per-filing orchestrator (used by main + backfill) |
| `scraper/main.py` | Cron entrypoint — last 24h of filings |
| `scraper/backfill.py` | One-time historical pull (--days N) |
| `.github/workflows/scrape.yml` | Every-2-hours cron |
| `.github/workflows/backfill.yml` | Manual trigger only |
| `seeds/0001_nifty50.sql` | One-time Nifty 50 companies seed |

## Operational notes

- **Scanned PDFs are skipped.** pymupdf returns empty text for image-only PDFs; we detect this (`< 100 chars total`) and move on. OCR (Tesseract / Vision API) deferred — most BSE filings are text-extractable.
- **Dedupe** is by MD5 of the PDF's source URL, stored as `filings.slug`. Re-running over the same window is safe (no double-insert).
- **Failures don't poison the queue.** A bad PDF or transient API error is logged and we move to the next filing.
- **The cron has an upper bound** of `SCRAPER_MAX_FILINGS_PER_RUN` (default 200) per run to keep within the 25-min GitHub Actions step timeout.
- **No PDFs are stored** on our side. Only metadata + extracted text chunks + embeddings.

## What's NOT here (future work)

- NSE scraping (BSE-only for now per D-013)
- Screener.in concall transcript scraper (T-013)
- OCR for scanned PDFs
- Per-sector custom classification rules
- Filing detection via RSS (currently pull-based)

---

Companion to **feed.normalguy.co.in** — Indian corporate filings + AI chat. See the main app repo for the Next.js code.
