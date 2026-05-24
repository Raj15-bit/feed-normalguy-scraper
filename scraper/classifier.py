"""3-layer label classifier (mapping → regex → DeepSeek fallback)."""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from scraper.config import get_config
from scraper.labels import LABEL_SLUGS

log = logging.getLogger(__name__)

# Layer 1: BSE category/subcategory → label. Built from real BSE data;
# covers ~70% of filings without any LLM call.
BSE_SUBCATEGORY_MAP: dict[str, str] = {
    # Results
    "financial results": "quarterly_results",
    "quarterly results": "quarterly_results",
    "audited results": "quarterly_results",
    # Concalls
    "earnings call transcript": "concall",
    "earnings call": "concall",
    "concall transcript": "concall",
    "investor / analyst call": "concall",
    "investor concall": "concall",
    # PPTs
    "investor presentation": "investor_ppt",
    "analyst presentation": "investor_ppt",
    # Annual reports
    "annual report": "annual_report",
    # Fundraising
    "fund raising": "fundraising",
    "fund raise": "fundraising",
    "issue of securities": "fundraising",
    "qip": "fundraising",
    "preferential issue": "fundraising",
    "rights issue": "fundraising",
    # M&A
    "acquisition": "ma",
    "merger": "ma",
    "amalgamation": "ma",
    "scheme of arrangement": "ma",
    # Orders
    "order win": "order_win",
    "new order": "order_win",
    "contract": "order_win",
    # Dividend
    "dividend": "dividend",
    "interim dividend": "dividend",
    "final dividend": "dividend",
    # Board
    "board meeting intimation": "board_meeting",
    "board meeting": "board_meeting",
    "outcome of board meeting": "board_meeting",
    # Directorate
    "change in directors": "directorate_change",
    "appointment": "directorate_change",
    "resignation": "directorate_change",
    "change in management": "directorate_change",
    # AGM/EGM
    "agm": "agm_egm",
    "annual general meeting": "agm_egm",
    "egm": "agm_egm",
    "extra ordinary general meeting": "agm_egm",
    "postal ballot": "agm_egm",
    # Credit rating
    "credit rating": "credit_rating",
    "rating": "credit_rating",
    # Insider trading
    "insider trading": "insider_trading",
    "trading window": "insider_trading",
    "sast disclosure": "insider_trading",
    "reg 7": "insider_trading",
    # Shareholding
    "shareholding pattern": "shareholding_pattern",
    "shp": "shareholding_pattern",
    # Splits/bonus
    "stock split": "stock_split_bonus",
    "bonus issue": "stock_split_bonus",
    "split / sub-division": "stock_split_bonus",
    "subdivision": "stock_split_bonus",
    # Capex
    "capacity expansion": "capex_expansion",
    "capex": "capex_expansion",
    "expansion": "capex_expansion",
    # Regulatory
    "regulation 30": "regulatory",
    "compliance": "regulatory",
    "sebi": "regulatory",
    "show cause notice": "regulatory",
    "penalty": "regulatory",
}

# Layer 2: regex over the title. Each entry: (label, compiled_pattern).
TITLE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("concall", re.compile(r"\b(concall|earnings call|conference call)\b", re.I)),
    ("investor_ppt", re.compile(r"\b(investor|analyst).*(presentation|deck)\b", re.I)),
    ("annual_report", re.compile(r"\bannual report\b", re.I)),
    ("quarterly_results", re.compile(r"\b(q[1-4]|quarterly|half[- ]?year|9m)\b.*results?\b", re.I)),
    ("quarterly_results", re.compile(r"\bunaudited.*results\b", re.I)),
    ("fundraising", re.compile(r"\b(qip|preferential issue|rights issue|fund\s*rais)\b", re.I)),
    ("ma", re.compile(r"\b(acquisition|merger|amalgamation|scheme of arrangement)\b", re.I)),
    ("order_win", re.compile(r"\b(order win|received order|contract\s+award|bags? a?\s*\w*\s*order)\b", re.I)),
    ("dividend", re.compile(r"\bdividend\b", re.I)),
    ("board_meeting", re.compile(r"\bboard meeting\b", re.I)),
    ("directorate_change", re.compile(r"\b(appointment of|resignation of|change in.*director)", re.I)),
    ("agm_egm", re.compile(r"\b(agm|egm|annual general meeting|postal ballot)\b", re.I)),
    ("credit_rating", re.compile(r"\b(credit rating|crisil|icra|care ratings|ind-?ra)\b", re.I)),
    ("insider_trading", re.compile(r"\b(insider trading|reg ?7|trading window|sast)\b", re.I)),
    ("shareholding_pattern", re.compile(r"\bshareholding pattern\b", re.I)),
    ("stock_split_bonus", re.compile(r"\b(stock split|bonus issue|sub-?division)\b", re.I)),
    ("capex_expansion", re.compile(r"\b(capex|capacity expansion|greenfield|brownfield)\b", re.I)),
    ("regulatory", re.compile(r"\b(sebi|penalty|show cause|reg\.? 30)\b", re.I)),
]


def classify_via_mapping(
    bse_category: Optional[str],
    bse_subcategory: Optional[str],
) -> Optional[str]:
    for raw in (bse_subcategory, bse_category):
        if not raw:
            continue
        key = raw.strip().lower()
        if key in BSE_SUBCATEGORY_MAP:
            return BSE_SUBCATEGORY_MAP[key]
    return None


def classify_via_regex(title: str) -> Optional[str]:
    for label, pat in TITLE_PATTERNS:
        if pat.search(title):
            return label
    return None


_deepseek: Optional[OpenAI] = None


def _deepseek_client() -> OpenAI:
    global _deepseek
    if _deepseek is None:
        cfg = get_config()
        _deepseek = OpenAI(
            api_key=cfg.deepseek_api_key,
            base_url="https://api.deepseek.com/v1",
        )
    return _deepseek


_DEEPSEEK_SYSTEM = (
    "You classify Indian stock market filings into exactly one of these labels: "
    + ", ".join(LABEL_SLUGS)
    + ". Return JSON like {\"label\": \"<one of the labels>\"}. No prose."
)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10), reraise=True)
def classify_via_llm(title: str, bse_subcategory: Optional[str]) -> str:
    prompt = (
        f"Title: {title}\nBSE subcategory: {bse_subcategory or 'n/a'}\nLabel:"
    )
    res = _deepseek_client().chat.completions.create(
        model="deepseek-chat",
        response_format={"type": "json_object"},
        temperature=0,
        messages=[
            {"role": "system", "content": _DEEPSEEK_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    )
    content = (res.choices[0].message.content or "").strip()
    try:
        parsed = json.loads(content)
        label = str(parsed.get("label", "")).strip()
        if label in LABEL_SLUGS:
            return label
    except json.JSONDecodeError:
        pass
    log.warning("DeepSeek returned unparseable/invalid label for %r: %r", title, content)
    return "other"


def classify(
    title: str,
    bse_category: Optional[str],
    bse_subcategory: Optional[str],
) -> str:
    """Returns one of the 18 LABEL_SLUGS, falling through three layers."""
    label = classify_via_mapping(bse_category, bse_subcategory)
    if label:
        return label
    label = classify_via_regex(title)
    if label:
        return label
    return classify_via_llm(title, bse_subcategory)
