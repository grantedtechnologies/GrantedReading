"""Reading-level measurement (no LLM calls).

word_frequency — table + hard/easy words
sentences     — long/short sentences
score         — grade estimate, bands, full report
dok           — question Depth of Knowledge helpers
"""

from .score import (
    ReadingLevelScore,
    analyze_passage,
    report_as_json,
    score_text,
    target_band,
)
from .word_frequency import ReadingLevelError, vocab_for_level

__all__ = [
    "ReadingLevelError",
    "ReadingLevelScore",
    "analyze_passage",
    "report_as_json",
    "score_text",
    "target_band",
    "vocab_for_level",
]
