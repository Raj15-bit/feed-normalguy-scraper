"""Centralized configuration loaded from environment variables."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    supabase_url: str
    supabase_service_key: str
    # openai_api_key is optional per DeepSeek-only rule (see
    # main repo docs/INFRASTRUCTURE.md). When None, embedder.embed_all()
    # returns Nones and filing_chunks.embedding is stored as NULL;
    # hybrid_search falls back to Postgres FTS.
    openai_api_key: Optional[str]
    deepseek_api_key: str
    max_filings_per_run: int
    batch_size_embeddings: int
    log_level: str
    ocr_enabled: bool
    ocr_max_pages: int
    ocr_dpi: int
    summary_enabled: bool
    fail_threshold: float


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required env var: {name}")
    return value


_cached: Optional[Config] = None


def get_config() -> Config:
    global _cached
    if _cached is not None:
        return _cached
    _cached = Config(
        supabase_url=_required("SUPABASE_URL"),
        supabase_service_key=_required("SUPABASE_SERVICE_KEY"),
        openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
        deepseek_api_key=_required("DEEPSEEK_API_KEY"),
        max_filings_per_run=int(os.environ.get("SCRAPER_MAX_FILINGS_PER_RUN", "200")),
        batch_size_embeddings=int(os.environ.get("SCRAPER_BATCH_SIZE_EMBEDDINGS", "100")),
        log_level=os.environ.get("SCRAPER_LOG_LEVEL", "INFO").upper(),
        ocr_enabled=os.environ.get("SCRAPER_OCR_ENABLED", "1") not in ("0", "false", "False"),
        ocr_max_pages=int(os.environ.get("SCRAPER_OCR_MAX_PAGES", "30")),
        ocr_dpi=int(os.environ.get("SCRAPER_OCR_DPI", "200")),
        summary_enabled=os.environ.get("SCRAPER_SUMMARY_ENABLED", "1") not in ("0", "false", "False"),
        fail_threshold=float(os.environ.get("SCRAPER_FAIL_THRESHOLD", "0.20")),
    )
    return _cached


def setup_logging() -> None:
    cfg = get_config()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
