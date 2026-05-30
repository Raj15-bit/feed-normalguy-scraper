"""2-4 sentence DeepSeek summary of a filing, written to filings.summary."""
from __future__ import annotations

import logging
from typing import Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from scraper.config import get_config

log = logging.getLogger(__name__)

_client: Optional[OpenAI] = None

# Cap context handed to DeepSeek. The deepseek-chat window is large but most
# value sits in the first few pages of a filing; this also bounds token cost.
_MAX_INPUT_CHARS = 12_000

_SYSTEM = (
    "You summarize Indian corporate filings for retail investors. "
    "Reply with 2-4 plain sentences covering what the filing is, the key "
    "numbers or decisions, and any direct investor impact. No preamble, "
    "no bullets, no markdown."
)


def _ds_client() -> OpenAI:
    global _client
    if _client is None:
        cfg = get_config()
        _client = OpenAI(api_key=cfg.deepseek_api_key, base_url="https://api.deepseek.com/v1")
    return _client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10), reraise=True)
def summarize(*, title: str, label: str, body: str) -> str:
    body = body[:_MAX_INPUT_CHARS]
    user = f"Title: {title}\nLabel: {label}\n\nFiling text:\n{body}"
    res = _ds_client().chat.completions.create(
        model="deepseek-chat",
        temperature=0.2,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    return (res.choices[0].message.content or "").strip()
