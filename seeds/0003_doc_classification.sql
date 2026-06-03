-- 0003_doc_classification.sql  (scraper-repo copy of app migration 0014)
-- Canonical source: feed-normal-guy/supabase/migrations/0014_doc_classification.sql
-- Applied to the live DB via the `migrate` or `doc-classify` workflow (psql).
--
-- Decide-once, store, read: persist the document TYPE and fiscal PERIOD that the
-- scraper decides from the PDF body at ingest, so the app stops re-guessing them
-- from the title + posting date on every render.
-- Additive + idempotent only. Never drops or rewrites existing columns/data.

-- Document type, decided from the body (see scraper/doc_period.py):
--   'transcript' | 'concall_audio' | 'investor_ppt' | 'annual_report'
--   | 'credit_rating' | 'notice' | 'other'
alter table filings add column if not exists doc_kind text;

-- Reported fiscal period. Nullable: annual reports (and anything we can't pin
-- down) legitimately have no quarter. fiscal_year = FY the period belongs to
-- (2026 = FY26, ending 31 Mar 2026); fiscal_quarter is 1..4.
alter table filings add column if not exists fiscal_year int;
alter table filings add column if not exists fiscal_quarter int;

-- How the period was determined: 'title' | 'body' | 'date_inferred'.
alter table filings add column if not exists period_source text;

-- Fast flag for the Concalls surface (= doc_kind 'transcript').
alter table filings add column if not exists is_transcript boolean not null default false;

-- Lookups for the company documents board: per-company, by type + period.
create index if not exists filings_doc_period_idx
  on filings (company_id, doc_kind, fiscal_year, fiscal_quarter);
