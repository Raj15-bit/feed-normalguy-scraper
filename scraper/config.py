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
    openai_api_key: str
    deepseek_api_key: str
    max_filings_per_run: int
    batch_size_embeddings: int
    log_level: str


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
        openai_api_key=_required("OPENAI_API_KEY"),
        deepseek_api_key=_required("DEEPSEEK_API_KEY"),
        max_filings_per_run=int(os.environ.get("SCRAPER_MAX_FILINGS_PER_RUN", "200")),
        batch_size_embeddings=int(os.environ.get("SCRAPER_BATCH_SIZE_EMBEDDINGS", "100")),
        log_level=os.environ.get("SCRAPER_LOG_LEVEL", "INFO").upper(),
    )
    return _cached


def setup_logging() -> None:
    cfg = get_config()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
