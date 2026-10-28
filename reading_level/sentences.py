"""Find sentences that are too long or too short for a target grade."""

from __future__ import annotations

import re
from typing import Any

from .word_frequency import tokenize_words

# Keep local so this file does not import score (score imports us).
_MAX_SENTENCE_LENGTH_INTERCEPT = 8.0
_MAX_SENTENCE_LENGTH_SLOPE = 1.0
_MIN_SENTENCE_LENGTH = 4

_ABBREV = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|etc|e\.g|i\.e|U\.S|U\.K)\.",
    re.IGNORECASE,
)


def split_sentences(text: str) -> list[str]:
    """Split plain text into sentences without spaCy.

    Uses a light end-of-sentence rule after protecting common abbreviations.
    """
    raw = (text or "").strip()
    if not raw:
        return []

    # Protect periods inside abbreviations so we don't split on them.
    protected = _ABBREV.sub(lambda m: m.group(0).replace(".", "\u0000"), raw)
    parts = re.split(r"(?<=[.!?])\s+", protected)
    out: list[str] = []
    for part in parts:
        s = part.replace("\u0000", ".").strip()
        if s:
            out.append(s)
    return out


def max_sentence_words(grade: float) -> int:
    """Longer grades allow longer sentences."""
    g = max(0.0, min(12.0, float(grade)))
    return int(
        round(_MAX_SENTENCE_LENGTH_INTERCEPT + _MAX_SENTENCE_LENGTH_SLOPE * g)
    )


def min_sentence_words(grade: float) -> int:
    """Very short choppy sentences are flagged when aiming higher."""
    g = max(0.0, min(12.0, float(grade)))
    # Kindergarten can be very short; by middle school prefer at least ~4–6.
    return max(_MIN_SENTENCE_LENGTH, int(round(3 + g * 0.25)))


def mean_sentence_length(text: str) -> float:
    sents = split_sentences(text)
    if not sents:
        return 0.0
    lengths = [len(tokenize_words(s)) for s in sents]
    return sum(lengths) / len(lengths)


def find_long_sentences(
    text: str,
    grade: float,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Sentences longer than the grade max, longest first."""
    max_len = max_sentence_words(grade)
    offenders: list[dict[str, Any]] = []
    for s in split_sentences(text):
        n = len(tokenize_words(s))
        if n > max_len:
            offenders.append(
                {
                    "sentence": s,
                    "word_count": n,
                    "max_allowed": max_len,
                    "reason": "too_long",
                }
            )
    offenders.sort(key=lambda x: -x["word_count"])
    return offenders[:limit]


def find_short_sentences(
    text: str,
    grade: float,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Sentences shorter than the grade min (choppy prose)."""
    min_len = min_sentence_words(grade)
    offenders: list[dict[str, Any]] = []
    for s in split_sentences(text):
        n = len(tokenize_words(s))
        # Ignore one-word titles / fragments
        if 0 < n < min_len:
            offenders.append(
                {
                    "sentence": s,
                    "word_count": n,
                    "min_expected": min_len,
                    "reason": "too_short",
                }
            )
    offenders.sort(key=lambda x: x["word_count"])
    return offenders[:limit]
