"""Score a passage: grade estimate, band check, and offender lists."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .sentences import (
    find_long_sentences,
    find_short_sentences,
    mean_sentence_length,
    split_sentences,
)
from .word_frequency import (
    find_easy_words,
    find_hard_words,
    mean_log_freq,
    tokenize_words,
    vocab_for_level,
)

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
FREQUENCY_TABLE_PATH = DATA_DIR / "frequency_table.json.gz"
FIXTURES_DIR = DATA_DIR / "fixtures"

MIN_RELIABLE_WORDS = 50

# estimated_grade = INTERCEPT + COEF_WORD_FREQ * mean_log_word_freq
#                 + COEF_SENTENCE_LENGTH * mean_sentence_length
GRADE_COEF_WORD_FREQ = -2.60
GRADE_COEF_SENTENCE_LENGTH = 0.50
GRADE_INTERCEPT = 4.46
GRADE_FLOOR = 0.0
GRADE_CEILING = 13.0

BAND_DEFINITIONS = (
    ("grade_k", "Kindergarten", 0.0),
    ("grade_1", "Grade 1", 1.0),
    ("grade_2", "Grade 2", 2.0),
    ("grade_3", "Grade 3", 3.0),
    ("grade_4", "Grade 4", 4.0),
    ("grade_5", "Grade 5", 5.0),
    ("grade_6", "Grade 6", 6.0),
    ("grade_7", "Grade 7", 7.0),
    ("grade_8", "Grade 8", 8.0),
    ("grade_9", "Grade 9", 9.0),
    ("grade_10", "Grade 10", 10.0),
    ("grade_11", "Grade 11", 11.0),
    ("grade_12", "Grade 12", 12.0),
)
BAND_HALF_WIDTH = 0.5

# Question stems are short; allow a wider band when scoring them.
QUESTION_BAND_TOLERANCE = 1.5


@dataclass(frozen=True)
class Band:
    label: str
    display: str
    low: float
    high: float

    @property
    def center(self) -> float:
        return (self.low + self.high) / 2.0


def _build_bands() -> tuple[Band, ...]:
    return tuple(
        Band(
            label=label,
            display=display,
            low=grade - BAND_HALF_WIDTH,
            high=grade + BAND_HALF_WIDTH,
        )
        for label, display, grade in BAND_DEFINITIONS
    )


BANDS: tuple[Band, ...] = _build_bands()
_BY_LABEL = {band.label: band for band in BANDS}
_WORD_ALIASES = {
    "k": "grade_k",
    "kindergarten": "grade_k",
    "kg": "grade_k",
}


def target_band(grade_level: int | str) -> Band:
    if isinstance(grade_level, bool):
        raise ValueError(f"Invalid grade level: {grade_level!r}")

    if isinstance(grade_level, (int, float)):
        numeric = float(grade_level)
    else:
        raw = str(grade_level).strip().lower()
        if not raw:
            raise ValueError("Grade level is required")
        alias = _WORD_ALIASES.get(raw)
        if alias:
            return _BY_LABEL[alias]
        if raw in _BY_LABEL:
            return _BY_LABEL[raw]
        match = re.match(r"^(\d+)", raw)
        if not match:
            raise ValueError(f"Invalid grade level: {grade_level!r}")
        numeric = float(match.group(1))

    for band in BANDS:
        if abs(band.center - numeric) < 1e-9:
            return band
    raise ValueError(f"Grade level out of supported range: {grade_level!r}")


def in_band(score, band: Band) -> bool:
    return band.low <= score.estimated_grade <= band.high


def widen(band: Band, tolerance: float) -> Band:
    return Band(
        label=band.label,
        display=band.display,
        low=band.low - tolerance,
        high=band.high + tolerance,
    )


@dataclass(frozen=True)
class ReadingLevelScore:
    estimated_grade: float
    confidence: str  # "high" or "low"
    mean_log_word_freq: float
    mean_sentence_length: float
    word_count: int
    sentence_count: int


def estimate_grade(text: str) -> ReadingLevelScore:
    """Uncalibrated grade from mean log-freq and mean sentence length."""
    tokens = tokenize_words(text)
    sents = split_sentences(text)
    mean_lf = mean_log_freq(tokens)
    mean_sl = mean_sentence_length(text)

    raw = (
        GRADE_INTERCEPT
        + GRADE_COEF_WORD_FREQ * mean_lf
        + GRADE_COEF_SENTENCE_LENGTH * mean_sl
    )
    grade = max(GRADE_FLOOR, min(GRADE_CEILING, raw))
    confidence = "high" if len(tokens) >= MIN_RELIABLE_WORDS else "low"

    return ReadingLevelScore(
        estimated_grade=round(grade, 2),
        confidence=confidence,
        mean_log_word_freq=round(mean_lf, 3),
        mean_sentence_length=round(mean_sl, 2),
        word_count=len(tokens),
        sentence_count=len(sents),
    )


score_text = estimate_grade


def analyze_passage(
    text: str,
    target_grade: int | float | str,
) -> dict[str, Any]:
    """Full report for the rewriter: score, band, offenders, level vocab."""
    band = target_band(target_grade)
    score = estimate_grade(text)
    return {
        "estimated_grade": score.estimated_grade,
        "confidence": score.confidence,
        "mean_log_word_freq": score.mean_log_word_freq,
        "mean_sentence_length": score.mean_sentence_length,
        "word_count": score.word_count,
        "sentence_count": score.sentence_count,
        "target_band": band.display,
        "target_band_label": band.label,
        "target_low": band.low,
        "target_high": band.high,
        "in_band": in_band(score, band),
        "below_target": score.estimated_grade < band.low,
        "too_hard": {
            "words": find_hard_words(text, band.center),
            "sentences": find_long_sentences(text, band.center),
        },
        "too_easy": {
            "words": find_easy_words(text, band.center),
            "sentences": find_short_sentences(text, band.center),
        },
        "level_vocab": vocab_for_level(band.center),
        "score": score,
        "band": band,
    }


def report_as_json(report: dict[str, Any]) -> dict[str, Any]:
    skip = {"score", "band"}
    return {k: v for k, v in report.items() if k not in skip}
