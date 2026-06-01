"""Supabase service-role client + DAO functions used by the scraper."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from supabase import Client, create_client

from scraper.config import get_config

log = logging.getLogger(__name__)

_client: Optional[Client] = None


def supabase() -> Client:
    global _client
    if _client is None:
        cfg = get_config()
        _client = create_client(cfg.supabase_url, cfg.supabase_service_key)
    return _client


@dataclass
class Company:
    id: str
    slug: str
    name: str
    bse_code: Optional[str]
    nse_symbol: Optional[str]


def list_companies(only_with_bse: bool = True) -> list[Company]:
    q = supabase().table("companies").select("id,slug,name,bse_code,nse_symbol")
    if only_with_bse:
        q = q.not_.is_("bse_code", "null")
    res = q.execute()
    rows = res.data or []
    return [
        Company(
            id=r["id"],
            slug=r["slug"],
            name=r["name"],
            bse_code=r.get("bse_code"),
            nse_symbol=r.get("nse_symbol"),
        )
        for r in rows
    ]


def filing_slug_for(source_url: str) -> str:
    return hashlib.md5(source_url.encode("utf-8")).hexdigest()


def filing_exists(slug: str) -> bool:
    res = (
        supabase()
        .table("filings")
        .select("id")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    return bool(res.data)


def insert_filing(
    *,
    company_id: str,
    slug: str,
    title: str,
    label: str,
    source_url: str,
    posted_at: datetime,
    page_count: int,
    labels: Optional[list[str]] = None,
    bse_category: Optional[str] = None,
    bse_subcategory: Optional[str] = None,
    ai_headline: Optional[str] = None,
    ai_summary_bullets: Optional[list[str]] = None,
) -> str:
    """Inserts a filing row and returns its id.

    Writes the headline + bullet summary into the app's canonical columns
    (ai_headline / ai_summary_bullets / ai_summary_generated_at — migration
    0006), which is what the home feed card renders. Also seeds the multi-label
    `labels` array (migration 0003) with the primary label so cards show a
    tick-mark badge immediately; a later re-classify pass can widen it.
    """
    from datetime import timezone

    row: dict[str, Any] = {
        "company_id": company_id,
        "slug": slug,
        "title": title,
        "label": label,
        "labels": labels if labels else [label],
        "source_url": source_url,
        "posted_at": posted_at.isoformat(),
        "page_count": page_count,
        "bse_category": bse_category,
        "bse_subcategory": bse_subcategory,
    }
    if ai_headline:
        row["ai_headline"] = ai_headline
        row["ai_summary_bullets"] = ai_summary_bullets or []
        row["ai_summary_generated_at"] = datetime.now(timezone.utc).isoformat()
    res = supabase().table("filings").insert(row).execute()
    if not res.data:
        raise RuntimeError(f"insert_filing returned no row for slug={slug}")
    return res.data[0]["id"]


def upsert_content_cache(
    *,
    source_url: str,
    extracted_text: str,
    content_type: str = "pdf",
    fetch_method: str = "pdf-parse",
) -> None:
    """Pre-warm the app's filing_content_cache (migration 0008) with the text we
    already extracted during scraping. The AI chat reads this table by
    source_url, so pre-seeding it means chats never wait on a live PDF download.

    Keyed on source_url (unique). We refresh on conflict so a re-scrape with
    better text wins. Best-effort: never raises into the pipeline.
    """
    text = (extracted_text or "").strip()
    if len(text) < 100:
        return
    try:
        supabase().table("filing_content_cache").upsert(
            {
                "source_url": source_url,
                "content_type": content_type,
                "fetch_method": fetch_method,
                "extracted_text": text,
                "char_count": len(text),
                "fetch_error": None,
            },
            on_conflict="source_url",
        ).execute()
    except Exception as e:  # noqa: BLE001 — cache is best-effort
        log.warning("upsert_content_cache failed for %s: %s", source_url, e)


def fetch_concall_candidates(
    *, limit: int, offset: int
) -> list[dict[str, Any]]:
    """Filings that carry the 'concall' label (primary or secondary) — the pool
    the re-titling pass scans for hidden transcripts. Newest first, paginated."""
    res = (
        supabase()
        .table("filings")
        .select("id,title,label,labels,source_url,company_id")
        .contains("labels", ["concall"])
        .order("posted_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return res.data or []


def get_cached_text(source_url: str) -> Optional[str]:
    """The extracted text we pre-warmed in filing_content_cache, if present."""
    res = (
        supabase()
        .table("filing_content_cache")
        .select("extracted_text")
        .eq("source_url", source_url)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0].get("extracted_text")
    return None


def update_filing_label_title(
    *, filing_id: str, title: str, label: str, labels: list[str]
) -> None:
    """Re-title + re-label an existing filing (used to surface a hidden
    transcript into the Concalls section)."""
    supabase().table("filings").update(
        {"title": title, "label": label, "labels": labels}
    ).eq("id", filing_id).execute()


def fetch_filings_page(*, limit: int, offset: int) -> list[dict[str, Any]]:
    """All filings, newest first, paginated — for the global de-dup scan."""
    res = (
        supabase()
        .table("filings")
        .select("id,company_id,title,label,labels,source_url,posted_at")
        .order("posted_at", desc=True)
        .order("id", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return res.data or []


def delete_filing(filing_id: str) -> None:
    """Delete a filing and its chunks (chunks first, in case no FK cascade)."""
    supabase().table("filing_chunks").delete().eq("filing_id", filing_id).execute()
    supabase().table("filings").delete().eq("id", filing_id).execute()


def insert_chunks(
    *,
    filing_id: str,
    company_id: str,
    chunks: list[dict[str, Any]],
) -> None:
    """chunks: [{ "page": int|None, "text": str, "embedding": list[float] }, ...]"""
    if not chunks:
        return
    rows = [
        {
            "filing_id": filing_id,
            "company_id": company_id,
            "page": c.get("page"),
            "text": c["text"],
            "embedding": c["embedding"],
        }
        for c in chunks
    ]
    supabase().table("filing_chunks").insert(rows).execute()


def queue_alerts(filing_id: str) -> int:
    """Calls the queue_filing_alerts RPC; returns number of rows queued."""
    res = supabase().rpc("queue_filing_alerts", {"p_filing_id": filing_id}).execute()
    return int(res.data or 0)
