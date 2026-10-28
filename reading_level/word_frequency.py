"""Word frequency table + hard/easy word lists for a target grade."""

from __future__ import annotations

import bisect
import gzip
import json
import re
import threading
from pathlib import Path
from typing import Any

FREQUENCY_TABLE_PATH = Path(__file__).resolve().parent / "data" / "frequency_table.json.gz"
OOV_LOGFREQ = 0.11
LEVEL_VOCAB_PROMPT_LIMIT = 180


class ReadingLevelError(Exception):
    """Raised when leveling cannot finish (missing table, rewrite unavailable, etc.)."""

    def __init__(self, message, *, reason=None, score=None, band=None):
        super().__init__(message)
        self.reason = reason
        self.score = score
        self.band = band


_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_STOPWORDS = frozenset(
    """
    a an the and or but if then so as at by for from in into of on onto to with
    without about above after before between through during under over is are
    was were be been being have has had do does did will would can could should
    may might must shall this that these those it its he she they them their
    we us our you your i me my not no yes
    """.split()
)


class FrequencyTable:
    """O(1) lookup of how common a word is."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else FREQUENCY_TABLE_PATH
        self._entries: dict[str, dict] | None = None
        self._meta: dict = {}
        self._sorted_logfreqs: list[float] = []
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def meta(self) -> dict:
        self._ensure_loaded()
        return dict(self._meta)

    def __len__(self) -> int:
        self._ensure_loaded()
        return len(self._entries)

    def _ensure_loaded(self) -> None:
        if self._entries is not None:
            return
        with self._lock:
            if self._entries is not None:
                return
            if not self._path.exists():
                raise ReadingLevelError(
                    f"Frequency table not found at {self._path}. "
                    "Build it with: python -m reading_level.scripts.build_frequency_table "
                    "--source wordfreq"
                )
            with gzip.open(self._path, "rt", encoding="utf-8") as handle:
                raw = json.load(handle)
            self._meta = raw.pop("_meta", {})
            self._entries = raw
            self._sorted_logfreqs = sorted(
                entry["logfreq"] for entry in self._entries.values()
            )

    def logfreq(self, word: str) -> float:
        self._ensure_loaded()
        entry = self._entries.get(word.lower())
        if entry is None:
            return OOV_LOGFREQ
        return entry["logfreq"]

    def is_proper(self, word: str) -> bool:
        self._ensure_loaded()
        entry = self._entries.get(word.lower())
        if entry is None:
            return False
        return bool(entry.get("proper", False))

    def contains(self, word: str) -> bool:
        self._ensure_loaded()
        return word.lower() in self._entries

    def percentile(self, word: str) -> float:
        self._ensure_loaded()
        if not self._sorted_logfreqs:
            return 0.0
        value = self.logfreq(word)
        rank = bisect.bisect_left(self._sorted_logfreqs, value)
        return 100.0 * rank / len(self._sorted_logfreqs)

    def words_between(self, lo: float, hi: float) -> list[tuple[str, float]]:
        self._ensure_loaded()
        out: list[tuple[str, float]] = []
        for word, entry in self._entries.items():
            lf = float(entry["logfreq"])
            if lo <= lf <= hi:
                out.append((word, lf))
        return out


_default_table: FrequencyTable | None = None
_default_lock = threading.Lock()


def get_default_table() -> FrequencyTable:
    global _default_table
    if _default_table is not None:
        return _default_table
    with _default_lock:
        if _default_table is None:
            _default_table = FrequencyTable()
    return _default_table


def reset_default_table() -> None:
    global _default_table
    with _default_lock:
        _default_table = None


def tokenize_words(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text or "")]


def hard_logfreq_cutoff(grade: float) -> float:
    g = max(0.0, min(12.0, float(grade)))
    return 2.2 - (g / 12.0) * 1.5


def easy_logfreq_cutoff(grade: float) -> float:
    g = max(0.0, min(12.0, float(grade)))
    return 3.2 - (g / 12.0) * 0.4


def content_words(tokens: list[str], table: FrequencyTable | None = None) -> list[str]:
    table = table or get_default_table()
    out: list[str] = []
    for w in tokens:
        if len(w) < 2 or w in _STOPWORDS:
            continue
        if table.is_proper(w):
            continue
        out.append(w)
    return out


def mean_log_freq(tokens: list[str], table: FrequencyTable | None = None) -> float:
    table = table or get_default_table()
    words = content_words(tokens, table)
    if not words:
        return 0.0
    return sum(table.logfreq(w) for w in words) / len(words)


def find_hard_words(
    text: str,
    grade: float,
    *,
    table: FrequencyTable | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    table = table or get_default_table()
    cutoff = hard_logfreq_cutoff(grade)
    seen: dict[str, dict[str, Any]] = {}
    for w in content_words(tokenize_words(text), table):
        lf = table.logfreq(w)
        if lf >= cutoff:
            continue
        if w not in seen:
            seen[w] = {"word": w, "logfreq": round(lf, 3), "reason": "too_hard"}
    return sorted(seen.values(), key=lambda x: x["logfreq"])[:limit]


def find_easy_words(
    text: str,
    grade: float,
    *,
    table: FrequencyTable | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    table = table or get_default_table()
    cutoff = easy_logfreq_cutoff(grade)
    seen: dict[str, dict[str, Any]] = {}
    for w in content_words(tokenize_words(text), table):
        lf = table.logfreq(w)
        if lf <= cutoff:
            continue
        if w not in seen:
            seen[w] = {"word": w, "logfreq": round(lf, 3), "reason": "too_easy"}
    return sorted(seen.values(), key=lambda x: -x["logfreq"])[:limit]


def vocab_for_level(
    grade: float,
    *,
    table: FrequencyTable | None = None,
    limit: int | None = None,
) -> list[str]:
    table = table or get_default_table()
    limit = LEVEL_VOCAB_PROMPT_LIMIT if limit is None else limit
    hard_cut = hard_logfreq_cutoff(grade)
    easy_cut = easy_logfreq_cutoff(grade)
    lo = hard_cut
    hi = min(easy_cut, hard_cut + 1.2)

    candidates: list[tuple[float, str]] = []
    for word, lf in table.words_between(lo, hi):
        if table.is_proper(word) or word in _STOPWORDS or len(word) < 3:
            continue
        candidates.append((lf, word))

    mid = (lo + hi) / 2.0
    candidates.sort(key=lambda pair: (abs(pair[0] - mid), pair[1]))
    return [w for _, w in candidates[:limit]]
