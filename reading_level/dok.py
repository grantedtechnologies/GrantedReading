"""Deterministic DOK estimates and source-material guards.

Cue phrases and span overlap are heuristics, not a score. Thresholds are
UNCALIBRATED. This module never calls an LLM.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Literal

from worksheet_blocks import WorksheetBlock

from . import score as rl_score
from .sentences import split_sentences

logger = logging.getLogger(__name__)

SOURCE_MATERIAL_MAX_TOKENS = 45000
SOURCE_CHARS_PER_TOKEN = 4
DOK_MIX_TOLERANCE = 0.25
DOK_MIX_BY_GRADE = {
    0: {1: 90, 2: 10, 3: 0, 4: 0},
    1: {1: 80, 2: 20, 3: 0, 4: 0},
    2: {1: 70, 2: 25, 3: 5, 4: 0},
    3: {1: 55, 2: 35, 3: 10, 4: 0},
    4: {1: 45, 2: 40, 3: 15, 4: 0},
    5: {1: 35, 2: 40, 3: 20, 4: 5},
    6: {1: 30, 2: 40, 3: 25, 4: 5},
    7: {1: 25, 2: 40, 3: 30, 4: 5},
    8: {1: 20, 2: 40, 3: 30, 4: 10},
    9: {1: 15, 2: 35, 3: 35, 4: 15},
    10: {1: 15, 2: 30, 3: 40, 4: 15},
    11: {1: 10, 2: 30, 3: 40, 4: 20},
    12: {1: 10, 2: 25, 3: 40, 4: 25},
}

SpanMatch = Literal["single_span", "multi_span", "no_match"]
Confidence = Literal["high", "low"]

_WORD = re.compile(r"[A-Za-z][A-Za-z']*")
_STOP = frozenset(
    """
    a an the and or but if of to in on for from at by with as is was were be
    been being it its this that these those you your we they he she his her
    their them then than so not no do did does done have has had what which
    who whom whose when where why how a
    """.split()
)

# Longer / more specific cues first. UNCALIBRATED lookup, not a model.
_DOK_CUES: tuple[tuple[int, re.Pattern[str], str], ...] = (
    (
        4,
        re.compile(
            r"\b(have you ever|your own experience|in your life|real world|"
            r"compare your experience)\b",
            re.I,
        ),
        "real-world / personal connection",
    ),
    (
        3,
        re.compile(
            r"\b(infer|inference|evaluate|conclude|theme|motivation|"
            r"support your answer|evidence from the (text|passage|story))\b",
            re.I,
        ),
        "infer / evidence",
    ),
    (
        2,
        re.compile(
            r"\b(why did|how did|what caused|lead to|because|compare|"
            r"difference|sequence|after .* then)\b",
            re.I,
        ),
        "why/how / relationship",
    ),
    (
        1,
        re.compile(
            r"\b(who|what|when|where|which|name|list|identify|how many)\b",
            re.I,
        ),
        "who/what/when/where",
    ),
)


@dataclass(frozen=True)
class QuestionDiagnostic:
    block_id: str
    estimated_dok: int
    cue_signal: str | None
    span_match: SpanMatch
    confidence: Confidence
    unanswerable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class QuestionItem:
    """A stem plus any following multiple-choice lines."""

    question: WorksheetBlock
    choices: tuple[WorksheetBlock, ...] = ()


def count_source_tokens(text: str) -> int:
    """UNCALIBRATED char/token stand-in. Not a model tokenizer."""
    if not text:
        return 0
    size = SOURCE_CHARS_PER_TOKEN
    return max(1, (len(text) + size - 1) // size)


def check_source_material(text: str, *, required: bool) -> int:
    """Return the token count, or raise ValueError if the teacher must fix it."""
    stripped = (text or "").strip()
    if required and not stripped:
        raise ValueError(
            "Tests need source material (the chapter or notes this worksheet "
            "covers). Paste or upload it before continuing."
        )
    tokens = count_source_tokens(stripped) if stripped else 0
    logger.info(
        '{"stage": "source_tokens", "tokens": %s, "ceiling": %s}',
        tokens,
        SOURCE_MATERIAL_MAX_TOKENS,
    )
    if tokens > SOURCE_MATERIAL_MAX_TOKENS:
        raise ValueError(
            "Source material is too long "
            f"({tokens} tokens; ceiling is {SOURCE_MATERIAL_MAX_TOKENS}). "
            "Narrow it to the specific chapter or section this worksheet covers. "
            "It will not be truncated automatically."
        )
    return tokens


def default_dok_mix(target_grade) -> dict[int, int]:
    """Percent mix for a grade band. UNCALIBRATED."""
    band = rl_score.target_band(target_grade)
    grade = int(round(band.center))
    grade = min(12, max(0, grade))
    mix = DOK_MIX_BY_GRADE.get(grade) or DOK_MIX_BY_GRADE[6]
    return {int(level): int(share) for level, share in mix.items()}


def mix_to_counts(question_count: int, percents: dict[int, int]) -> dict[int, int]:
    """Largest-remainder allocation so counts sum to question_count."""
    if question_count <= 0:
        return {1: 0, 2: 0, 3: 0, 4: 0}
    shares = []
    for level in (1, 2, 3, 4):
        pct = max(0, int(percents.get(level, 0)))
        exact = question_count * pct / 100.0
        floor = int(exact)
        shares.append((level, floor, exact - floor))
    assigned = sum(item[1] for item in shares)
    leftover = question_count - assigned
    shares.sort(key=lambda item: item[2], reverse=True)
    counts = {level: floor for level, floor, _ in shares}
    for level, _, _ in shares:
        if leftover <= 0:
            break
        counts[level] += 1
        leftover -= 1
    return {level: counts.get(level, 0) for level in (1, 2, 3, 4)}


def group_question_items(blocks: list[WorksheetBlock]) -> list[QuestionItem]:
    items: list[QuestionItem] = []
    pending: WorksheetBlock | None = None
    choices: list[WorksheetBlock] = []
    for block in blocks:
        if block.block_type == "question":
            if pending is not None:
                items.append(QuestionItem(pending, tuple(choices)))
            pending = block
            choices = []
        elif block.block_type == "answer_choice" and pending is not None:
            choices.append(block)
        elif pending is not None and block.block_type != "answer_choice":
            items.append(QuestionItem(pending, tuple(choices)))
            pending = None
            choices = []
    if pending is not None:
        items.append(QuestionItem(pending, tuple(choices)))
    return items


def content_words(text: str) -> set[str]:
    return {
        word.lower()
        for word in _WORD.findall(text or "")
        if word.lower() not in _STOP and len(word) > 1
    }


def _content_overlap(query: set[str], hay: set[str]) -> set[str]:
    matched = set()
    for word in query:
        for other in hay:
            if word == other:
                matched.add(word)
            elif len(word) >= 3 and len(other) >= 3 and (
                other.startswith(word) or word.startswith(other)
            ):
                matched.add(word)
    return matched


def span_match(question: str, *corpora: str) -> SpanMatch:
    """Coarse overlap: one sentence, several, or none.

    This is the worksheet answerability stand-in. There was no span-recovery
    checker on the generation path. A single locatable overlap is a DOK-1
    signal, not proof the item is valid.
    """
    corpus = "\n".join(part for part in corpora if part)
    if not corpus.strip() or not (question or "").strip():
        return "no_match"
    query = content_words(question)
    if not query:
        return "no_match"
    hits = 0
    for sentence in split_sentences(corpus):
        overlap = _content_overlap(query, content_words(sentence))
        needed = 2 if len(query) >= 2 else 1
        if len(overlap) >= needed:
            hits += 1
    if hits == 0:
        return "no_match"
    if hits == 1:
        return "single_span"
    return "multi_span"


def match_dok_cue(text: str) -> tuple[int | None, str | None]:
    for level, pattern, label in _DOK_CUES:
        if pattern.search(text or ""):
            return level, label
    return None, None


def diagnose_question(
    block: WorksheetBlock, *corpora: str
) -> QuestionDiagnostic:
    cue_level, cue_signal = match_dok_cue(block.text)
    match = span_match(block.text, *corpora)
    estimated = cue_level or 1
    if match == "multi_span" and estimated < 2:
        estimated = 2
    if match == "single_span" and estimated >= 3:
        # Cue says inference but the stem still points at one sentence.
        confidence: Confidence = "low"
    elif cue_level and match != "no_match":
        confidence = "high"
    else:
        confidence = "low"
    return QuestionDiagnostic(
        block_id=block.block_id,
        estimated_dok=int(estimated),
        cue_signal=cue_signal,
        span_match=match,
        confidence=confidence,
        unanswerable=match == "no_match",
    )


def bucket_questions(
    items: list[QuestionItem], *corpora: str
) -> list[QuestionDiagnostic]:
    return [diagnose_question(item.question, *corpora) for item in items]


def target_dok_for_items(
    diagnostics: list[QuestionDiagnostic], counts: dict[int, int]
) -> list[int]:
    """Assign a target DOK to each item, converting the smallest number of stems.

    Keep a question at its estimate when that bucket still has a slot; fill
    leftover slots from the remaining questions.
    """
    remaining = dict(counts)
    targets = [0] * len(diagnostics)
    for index, diagnostic in enumerate(diagnostics):
        level = diagnostic.estimated_dok
        if remaining.get(level, 0) > 0:
            targets[index] = level
            remaining[level] -= 1
    for index, target in enumerate(targets):
        if target:
            continue
        for level in (1, 2, 3, 4):
            if remaining.get(level, 0) > 0:
                targets[index] = level
                remaining[level] -= 1
                break
        if not targets[index]:
            targets[index] = diagnostics[index].estimated_dok
    return targets


def mix_from_levels(levels: list[int]) -> dict[int, int]:
    tallies = {1: 0, 2: 0, 3: 0, 4: 0}
    for level in levels:
        if level in tallies:
            tallies[level] += 1
    return tallies


def mix_within_tolerance(actual: dict[int, int], target: dict[int, int], n: int) -> bool:
    if n <= 0:
        return True
    drift = sum(abs(actual.get(level, 0) - target.get(level, 0)) for level in (1, 2, 3, 4)) / 2
    return drift <= max(1, n * DOK_MIX_TOLERANCE)


def choice_label_and_text(text: str) -> tuple[str, str]:
    match = re.match(r"^(?:([A-Da-d])[.)]\s+|\(([A-Da-d])\)\s+)(.*)$", text.strip())
    if not match:
        return "", text.strip()
    label = (match.group(1) or match.group(2) or "").upper()
    return label, (match.group(3) or "").strip()


def check_distractors(
    choices: tuple[WorksheetBlock, ...], *corpora: str
) -> list[str]:
    """Minimal plausibility: each option should share some content with the corpus.

    There was no earlier distractor checker to reuse. Options that share no
    content words with the passage/source are flagged, as are exact duplicates.
    """
    problems = []
    corpus_words = content_words(" ".join(part for part in corpora if part))
    seen: set[str] = set()
    for choice in choices:
        _, body = choice_label_and_text(choice.text)
        key = body.lower()
        if key in seen:
            problems.append(f"{choice.block_id}: duplicate option")
        seen.add(key)
        overlap = _content_overlap(content_words(body), corpus_words)
        if body and corpus_words and not overlap:
            problems.append(f"{choice.block_id}: option has no overlap with the passage")
    return problems
