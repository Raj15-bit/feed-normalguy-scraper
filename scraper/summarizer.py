"""DeepSeek headline + bullet summary for a filing.

Mirrors the app's lib/summarize.ts contract exactly so cron-inserted filings
populate filings.ai_headline + filings.ai_summary_bullets (migration 0006),
the columns the home feed card actually renders. Returns (None, None) on any
failure — never blocks the insert.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from scraper.config import get_config

log = logging.getLogger(__name__)

_client: Optional[OpenAI] = None

# Same excerpt budget the app uses (lib/summarize.ts slices 4000 chars).
_MAX_INPUT_CHARS = 4_000

# Verbatim copy of lib/summarize.ts SYSTEM prompt.
_SYSTEM = (
    "You extract facts from Indian listed-company regulatory filings. Return "
    'STRICT JSON of shape: {"headline": "...", "bullets": ["...", "..."]}.\n\n'
    "Rules:\n"
    '- "headline" is ONE line, <= 80 characters, chyron-style. Use absolute '
    "dates and currency.\n"
    '- "bullets" is 3 to 5 short bullets, TOTAL <= 50 words across all bullets.\n'
    "- Only facts present in the input text. NO analysis, NO projections, NO "
    "speculation, NO recommendations.\n"
    "- If the filing is just a notice without details, give 1-2 bullets "
    "summarising the notice itself.\n"
    '- NEVER include phrases like "the company", "we believe", '
    '"investors should".'
)


def _ds_client() -> OpenAI:
    global _client
    if _client is None:
        cfg = get_config()
        _client = OpenAI(
            api_key=cfg.deepseek_api_key, base_url="https://api.deepseek.com/v1"
        )
    return _client


def _word_count(bullets: list[str]) -> int:
    return sum(len(b.split()) for b in bullets)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=10),
    reraise=True,
)
def _call(user: str) -> str:
    res = _ds_client().chat.completions.create(
        model="deepseek-chat",
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=400,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    return (res.choices[0].message.content or "").strip()


def summarize(
    *,
    title: str,
    label: str,
    body: str,
    bse_category: Optional[str] = None,
    bse_subcategory: Optional[str] = None,
) -> tuple[Optional[str], Optional[list[str]]]:
    """Return (headline, bullets) or (None, None) on failure."""
    excerpt = " ".join((body or "").split())[:_MAX_INPUT_CHARS]
    user = (
        f"Title: {title}\n"
        f"Labels: {label}\n"
        f"BSE Category: {bse_category or ''}\n"
        f"BSE Sub-Category: {bse_subcategory or ''}\n"
        f"Filing text excerpt:\n{excerpt}"
    )
    try:
        raw = _call(user)
    except Exception as e:
        log.warning("summarize: DeepSeek call failed: %s", e)
        return None, None
    if not raw:
        return None, None
    try:
        parsed = json.loads(raw)
    except Exception as e:
        log.warning("summarize: JSON parse failed: %s", e)
        return None, None

    headline = str(parsed.get("headline") or "").strip()[:80]
    bullets_raw = parsed.get("bullets") or []
    bullets = [
        str(b).strip()
        for b in bullets_raw
        if isinstance(b, str) and str(b).strip()
    ][:5]
    # Trim to keep total under ~50 words (mirror lib/summarize.ts clamp at 55).
    while len(bullets) > 1 and _word_count(bullets) > 55:
        bullets.pop()
    if not headline or not bullets:
        return None, None
    return headline, bullets
