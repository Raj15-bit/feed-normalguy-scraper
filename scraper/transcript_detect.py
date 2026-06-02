"""Detect whether a filing is an earnings-call TRANSCRIPT.

The Concalls section on feed.normalguy must contain ONLY real transcripts.
Problem: many companies file the transcript as a PDF *attached to* a generic
"Analysts/Institutional Investor Meet/Con. Call Updates" notice, so the TITLE
never says "Transcript" and a title-only check misses it.

This module detects transcripts from the title OR the extracted PDF body, and
normalises the stored title so the app's title-based Concall filter surfaces it.
Kept deliberately conservative so a results/PPT PDF is never mis-flagged.
"""
from __future__ import annotations

import re

# Title already names it a transcript — the easy, unambiguous case.
TRANSCRIPT_TITLE_RE = re.compile(r"\btranscript\b", re.I)

# A pre-event NOTICE: announces that results/a call WILL happen on a future
# date. Its attached PDF is the intimation letter, not a transcript — never
# retitle these as transcripts even if the body mentions the word.
_INTIMATION_TITLE_RE = re.compile(
    r"\b(to\s+announce|will\s+announce|intimation|prior\s+intimation|"
    r"schedule\s+of|notice\s+of|to\s+be\s+held|will\s+be\s+held|"
    r"date\s+of\s+(the\s+)?(board|meeting)|reschedul|postpone)\b",
    re.I,
)


def is_intimation_title(title: str) -> bool:
    """True when the title is a pre-event notice (announces a future date),
    not the content document itself."""
    return bool(_INTIMATION_TITLE_RE.search(title or ""))

# Body says "transcript" near earnings-call context.
_BODY_TRANSCRIPT_RE = re.compile(r"\btranscript\b", re.I)
_CALL_CONTEXT_RE = re.compile(
    r"\b(earnings call|conference call|investor call|analyst call|con\.?\s*call|"
    r"earnings conference|q[1-4]\s*fy\s*\d{2})\b",
    re.I,
)

# Structural fingerprints almost unique to a verbatim call transcript.
_STRUCTURE_RES = [
    re.compile(p, re.I)
    for p in (
        r"\bmoderator\b",
        r"\boperator\b",
        r"ladies and gentlemen",
        r"question[\s-]and[\s-]answer",
        r"\bthe next question\b",
        r"from the line of",
        r"\b(thank you|over to you)\b.*\bsir\b",
    )
]

# How much of the body to inspect (transcripts announce themselves up top).
_HEAD_CHARS = 6000


def looks_like_transcript(title: str, body: str | None = None) -> bool:
    """True if this filing is an earnings-call transcript (title or body)."""
    if TRANSCRIPT_TITLE_RE.search(title or ""):
        return True
    head = (body or "")[:_HEAD_CHARS]
    if not head:
        return False
    # "transcript" + a call-context phrase nearby.
    if _BODY_TRANSCRIPT_RE.search(head) and _CALL_CONTEXT_RE.search(head):
        return True
    # Two+ distinct verbatim-call structural markers.
    hits = sum(1 for p in _STRUCTURE_RES if p.search(head))
    return hits >= 2


def normalize_transcript_title(title: str) -> str:
    """Ensure the stored title carries the word 'Transcript' so the app's
    Concall filter (title ~ /transcript/i) surfaces it. Idempotent."""
    t = (title or "").strip()
    if not t:
        return "Transcript"
    if TRANSCRIPT_TITLE_RE.search(t):
        return t
    return f"Transcript — {t}"
