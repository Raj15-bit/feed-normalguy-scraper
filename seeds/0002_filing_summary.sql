-- Adds the filings.summary column written by the scraper's DeepSeek summarizer.
-- Paste this into Supabase Dashboard -> SQL Editor -> Run, once.

alter table public.filings
    add column if not exists summary text;
