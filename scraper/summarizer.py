"""DeepSeek headline + bullet summary for a filing.

Mirrors the app's lib/summarize.ts contract exactly so cron-inserted filings
populate filings.ai_headline + filings.ai_summary_bullets (migration 0006),
the columns the home feed card actually renders. Returns (None, None) on any
failure — never blocks the insert.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from scraper.config import get_config

log = logging.getLogger(__name__)

_client: Optional[OpenAI] = None

# Summaries run on the CHEAPEST flash tier — NOT the pricier auto-routed
# "deepseek-chat". Override on CI with DEEPSEEK_SUMMARY_MODEL_OVERRIDE.
_SUMMARY_MODEL = os.environ.get("DEEPSEEK_SUMMARY_MODEL_OVERRIDE", "deepseek-v4-flash")

# Excerpt budget, trimmed 12k → 8k chars to cut input-token cost ~⅓; a ~50-word
# summary doesn't need more context.
_MAX_INPUT_CHARS = 8_000

# Verbatim copy of lib/summarize.ts SYSTEM prompt (~50 word bullet context).
_SYSTEM = (
    "You are a financial news editor summarising an Indian listed-company "
    "regulatory filing for retail investors. Return STRICT JSON of shape: "
    '{"headline": "...", "bullets": ["...", "..."]}.\n\n'
    "Rules:\n"
    '- "headline" is ONE line, <= 80 characters, chyron-style. Use absolute '
    "dates and currency.\n"
    '- "bullets" is 2 to 3 bullets, TOTAL about 50 words across all bullets '
    "(never more than 60). Each bullet a short, specific sentence.\n"
    "- Explain what is actually happening and why it matters factually: the "
    "event, the key numbers (amounts, %, dates, quarters), named parties, "
    "agencies, ratings. Keep it tight — only the most important facts.\n"
    "- Only facts present in the input text. NO analysis, NO projections, NO "
    "speculation, NO buy/sell recommendations.\n"
    "- If the input text is thin (only a title/notice), still write the best "
    "factual summary you can from it; do not invent details.\n"
    '- NEVER include filler like "we believe", "investors should", or '
    '"this is important".'
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
        model=_SUMMARY_MODEL,
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=800,
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
    ][:3]
    # Keep total within the ~50-word target (mirror lib/summarize.ts clamp at 60).
    while len(bullets) > 1 and _word_count(bullets) > 60:
        bullets.pop()
    if not headline or not bullets:
        return None, None
    return headline, bullets
