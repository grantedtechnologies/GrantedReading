"""Worksheet + leveling pipeline.

Uses reading_level.score for measurement and llm.py for Foundry calls.
Illustration calls go through image_gen.py.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import file
from image_gen import create_image_prompt, generate_image
from llm import FOUNDRY_DOWN, generate_json, generate_text, user_error_message
from reading_level import ReadingLevelError, score_text
from reading_level import dok as rl_dok
from reading_level import score as rl_score
from reading_level.word_frequency import LEVEL_VOCAB_PROMPT_LIMIT, vocab_for_level
from worksheet_blocks import BLOCK_TYPES, WorksheetBlock, segment_worksheet_bytes

logger = logging.getLogger(__name__)


STORY_MAX_CHARS = 900
QUESTION_MAX_CHARS = 140
TITLE_MAX_CHARS = 60

WORKSHEET_SYSTEM_PROMPT = f"""You are a reading comprehension worksheet generator for special education teachers.

Given information about a student (grade, reading level, and interests), write a short fiction story and reading comprehension questions.

Rules:
- Write the story at the student's READING LEVEL (not their grade level).
- Incorporate the student's interests naturally into the story.
- Keep the story engaging and age-appropriate for the student's grade.
- Write ordinary comprehension questions about the story. Do not force a Who / What / When / Where / Why template — pick stems that fit the story.
- Each question must end with a question mark and be answerable from the story.
- The story must be one paragraph with no line breaks.
- The finished worksheet must fit on ONE printed page. Keep the story under {STORY_MAX_CHARS} characters (about 150–170 words). Keep the title under {TITLE_MAX_CHARS} characters. Keep each question under {QUESTION_MAX_CHARS} characters.
- Do not include notes, analysis, rewriting steps, or thinking — only the final title, story, and questions.
- If a focus vocabulary word is given, use that exact word 3 to 5 times in the story. Weave it in naturally so the reader meets it in context. Do not replace it with a simpler synonym.
- If a phonics pattern is given, include 3 to 5 different words that contain that exact letter pattern (for example, "tion" in station, mention, action). Use those words naturally so the reader practices the pattern.

Respond with ONLY valid JSON (no markdown, no extra text) in this exact shape:
{{
  "title": "Story title",
  "story": "The full story text",
  "questions": {{
    "q1": "...?",
    "q2": "...?",
    "q3": "...?",
    "q4": "...?",
    "q5": "...?"
  }}
}}"""

GenerateFn = Callable[..., str]


@dataclass
class CorrectionResult:
    """Thin stand-in so older callers (modulate / correct route) keep working."""

    text: str
    initial_score: rl_score.ReadingLevelScore
    final_score: rl_score.ReadingLevelScore
    target_band: Any
    gate_passed: bool
    failure_reason: str | None = None
    edits: list = field(default_factory=list)
    iterations: int = 0
    score_trajectory: list = field(default_factory=list)
    protected_terms_preserved: list = field(default_factory=list)
    in_band: bool = False

    def __post_init__(self):
        self.in_band = self.gate_passed


@dataclass
class LevelOutcome:
    """Result of leveling a passage."""

    text: str
    correction: CorrectionResult
    final_score: rl_score.ReadingLevelScore
    gate_passed: bool
    target_band: Any
    failure_reason: str | None = None
    llm_rewrite_applied: bool = False
    first_report: dict | None = None
    final_report: dict | None = None


_REWRITE_SYSTEM = """You rewrite student reading passages to match a target grade band.

Rules:
- Keep the same overall meaning, characters, and plot.
- Do not add a title or questions.
- Prefer the level vocabulary list when you need everyday words.
- Replace words marked too_hard with simpler ones from level_vocab when possible.
- Fix sentences marked too_long (split or shorten) or too_short (combine or expand).
- Preserve every protected term exactly as written (spelling unchanged).
- Return ONLY the rewritten passage as plain text. No JSON, no markdown."""


def _brief_for_prompt(report: dict[str, Any], protected_terms: list[str]) -> str:
    """Compact JSON the model can follow."""
    hard_words = [w["word"] for w in report["too_hard"]["words"][:25]]
    easy_words = [w["word"] for w in report["too_easy"]["words"][:15]]
    long_sents = [s["sentence"] for s in report["too_hard"]["sentences"][:8]]
    short_sents = [s["sentence"] for s in report["too_easy"]["sentences"][:8]]
    vocab = report.get("level_vocab") or []

    direction = (
        "The passage scores BELOW the band — make vocabulary and sentences "
        "a bit richer (avoid padding with only ultra-common words)."
        if report["below_target"]
        else "The passage scores ABOVE the band — simplify hard words and "
        "shorten long sentences."
    )

    payload = {
        "direction": direction,
        "target_band": report["target_band"],
        "estimated_grade": report["estimated_grade"],
        "band_range": [report["target_low"], report["target_high"]],
        "too_hard_words": hard_words,
        "too_easy_words": easy_words,
        "too_long_sentences": long_sents,
        "too_short_sentences": short_sents,
        "prefer_these_words": vocab[:80],
        "protected_terms": list(protected_terms or []),
    }
    return json.dumps(payload, indent=2)


def _rewrite_once(
    text: str,
    report: dict[str, Any],
    protected_terms: list[str],
    generate: GenerateFn,
) -> str:
    user = (
        f"Rewrite this passage for {report['target_band']}.\n\n"
        f"Brief:\n{_brief_for_prompt(report, protected_terms)}\n\n"
        f"Passage:\n{text.strip()}"
    )
    return generate(user, system=_REWRITE_SYSTEM).strip()


def level_story(
    text: str,
    target_grade: int | float | str,
    *,
    protected_terms: list[str] | None = None,
    allow_rewrite: bool = True,
    generate: GenerateFn | None = None,
    passage_type: str | None = None,
) -> LevelOutcome:
    """Score → optional one-shot AI rewrite → re-score.

    Never raises on a band miss when allow_rewrite is used for teacher review:
    returns the best text with gate_passed=False. Callers that need a hard
    guarantee can check gate_passed themselves.
    """
    del passage_type  # kept for call-site compatibility; not used by this pipeline
    protected = list(protected_terms or [])
    band = rl_score.target_band(target_grade)
    first = rl_score.analyze_passage(text, target_grade)
    initial_score = first["score"]

    if first["in_band"] or not allow_rewrite:
        result = CorrectionResult(
            text=text,
            initial_score=initial_score,
            final_score=initial_score,
            target_band=band,
            gate_passed=first["in_band"],
            failure_reason=None if first["in_band"] else "outside_band",
            protected_terms_preserved=[
                t for t in protected if t.lower() in text.lower()
            ],
        )
        return LevelOutcome(
            text=text,
            correction=result,
            final_score=initial_score,
            gate_passed=first["in_band"],
            target_band=band,
            failure_reason=result.failure_reason,
            llm_rewrite_applied=False,
            first_report=rl_score.report_as_json(first),
            final_report=rl_score.report_as_json(first),
        )

    if generate is None:
        raise ReadingLevelError(
            "A rewrite was needed but no generate function was provided.",
            reason="rewrite_unavailable",
            score=initial_score,
            band=band,
        )

    rewritten = text
    applied = False
    try:
        rewritten = _rewrite_once(text, first, protected, generate)
        applied = rewritten.strip() != text.strip()
    except Exception:
        logger.exception("Reading-level rewrite failed; keeping original")
        result = CorrectionResult(
            text=text,
            initial_score=initial_score,
            final_score=initial_score,
            target_band=band,
            gate_passed=False,
            failure_reason="rewrite_call_failed",
            protected_terms_preserved=[
                t for t in protected if t.lower() in text.lower()
            ],
        )
        return LevelOutcome(
            text=text,
            correction=result,
            final_score=initial_score,
            gate_passed=False,
            target_band=band,
            failure_reason="rewrite_call_failed",
            llm_rewrite_applied=False,
            first_report=rl_score.report_as_json(first),
            final_report=rl_score.report_as_json(first),
        )

    # Re-assert protected terms if the model dropped them.
    final_text = rewritten
    for term in protected:
        if term and term.lower() not in final_text.lower() and term in text:
            final_text = f"{final_text.rstrip()} {term}"

    final = rl_score.analyze_passage(final_text, target_grade)
    final_score = final["score"]
    result = CorrectionResult(
        text=final_text,
        initial_score=initial_score,
        final_score=final_score,
        target_band=band,
        gate_passed=final["in_band"],
        failure_reason=None if final["in_band"] else "outside_band",
        protected_terms_preserved=[
            t for t in protected if t.lower() in final_text.lower()
        ],
    )
    return LevelOutcome(
        text=final_text,
        correction=result,
        final_score=final_score,
        gate_passed=final["in_band"],
        target_band=band,
        failure_reason=result.failure_reason,
        llm_rewrite_applied=applied,
        first_report=rl_score.report_as_json(first),
        final_report=rl_score.report_as_json(final),
    )


def correct_text(
    text: str,
    target_grade: int | float | str,
    *,
    protected_terms: list[str] | None = None,
) -> CorrectionResult:
    """Measure only (no AI). Used for short answer-choice snippets."""
    outcome = level_story(
        text,
        target_grade,
        protected_terms=protected_terms,
        allow_rewrite=False,
    )
    return outcome.correction


def measured_without_repair(
    text: str,
    target_grade: int | float | str,
    *,
    protected_terms: list[str] | None = None,
) -> CorrectionResult:
    """Same as correct_text — name kept for older call sites."""
    return correct_text(text, target_grade, protected_terms=protected_terms)


def leveling_report_for_ui(
    original_story: str,
    outcome: LevelOutcome,
) -> dict[str, Any]:
    """Before/after numbers for the worksheet review panel."""
    first = outcome.first_report or {}
    final = outcome.final_report or {}
    band = outcome.target_band
    first_grade = first.get("estimated_grade", outcome.correction.initial_score.estimated_grade)
    return {
        "target_band": band.display,
        "first_draft": {
            "estimated_grade": first_grade,
            "confidence": first.get(
                "confidence", outcome.correction.initial_score.confidence
            ),
            "in_band": bool(first.get("in_band", False)),
            "below_target": bool(first.get("below_target", False)),
            "story": original_story,
            "too_hard": first.get("too_hard"),
            "too_easy": first.get("too_easy"),
        },
        "final": {
            "estimated_grade": final.get(
                "estimated_grade", outcome.final_score.estimated_grade
            ),
            "confidence": final.get("confidence", outcome.final_score.confidence),
            "in_band": outcome.gate_passed,
            "too_hard": final.get("too_hard"),
            "too_easy": final.get("too_easy"),
        },
        "improved": original_story.strip() != outcome.text.strip(),
        "llm_rewrite_applied": outcome.llm_rewrite_applied,
    }


def format_level_vocab_for_prompt(target_grade: int | float | str) -> str:
    """Short block to append to worksheet generation prompts."""
    report_grade = target_grade
    try:
        band = rl_score.target_band(target_grade)
        words = vocab_for_level(band.center, limit=LEVEL_VOCAB_PROMPT_LIMIT)
    except Exception:
        logger.exception("Could not load level vocabulary for %s", report_grade)
        return ""
    if not words:
        return ""
    sample = ", ".join(words[:100])
    return (
        f" Prefer everyday words appropriate for {band.display}, such as: "
        f"{sample}. You do not need to use all of them; use them naturally."
    )


def parse_focus_vocabulary(raw) -> list[str]:
    """Teacher-chosen words to repeat in the story and leave untouched.

    Commas separate terms. Caps at three so the story does not become a list.
    """
    if not raw:
        return []
    terms = []
    seen = set()
    for part in str(raw).replace(";", ",").split(","):
        term = " ".join(part.split())
        if not term or len(term) > 40:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) == 3:
            break
    return terms


def parse_focus_phonics(raw) -> str:
    """One letter pattern to practice, such as tion or ing.

    Takes the first 2-12 letter chunk so a dropdown label like
    'tion — action, station' still yields tion. Single letters are
    rejected so a stray 'a' does not match every word.
    """
    if not raw:
        return ""
    match = _PHONICS_CHUNK.search(str(raw).strip())
    return match.group(0).lower() if match else ""


def words_matching_phonics(text, pattern) -> list[str]:
    """Unique story words that contain the phonics chunk, first spelling kept."""
    chunk = parse_focus_phonics(pattern)
    if not chunk or not text:
        return []
    seen = set()
    found = []
    for word in _STORY_WORD.findall(text):
        key = word.lower()
        if chunk in key and key not in seen:
            seen.add(key)
            found.append(word)
    return found


def _unique_protected_terms(*groups) -> list[str]:
    seen = set()
    terms = []
    for group in groups:
        for term in group or []:
            key = str(term).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            terms.append(term)
    return terms


DOK_LEVELS = (1, 2, 3, 4)

DOK_INSTRUCTIONS = {
    1: (
        "DOK 1 — Recall & Reproduction\n"
        "The answer is explicitly stated in the text, typically in a single "
        "sentence or phrase. The question asks the student to locate or recall "
        "a fact, definition, or detail directly from the passage. Example "
        'shape: "What did Leo ask Maya to do?"'
    ),
    2: (
        "DOK 2 — Skill & Concept (Basic Reasoning)\n"
        "The answer requires connecting two or more pieces of information from "
        "the text, or recognizing a relationship — cause and effect, sequence, "
        "comparison. Not stated in a single sentence, but a short, direct "
        "logical step from what's stated. Example shape: \"Why did Leo take a "
        "deep breath before walking over to Maya?\""
    ),
    3: (
        "DOK 3 — Strategic Thinking (Inference & Analysis)\n"
        "The answer requires synthesizing information from multiple parts of "
        "the passage, drawing a conclusion the text supports but doesn't state "
        "outright, or evaluating motivation or theme with textual "
        "justification. The question should explicitly ask the student to "
        "support their answer with evidence from the text. Example shape: "
        '"What can you infer about how Leo felt about himself by the end of '
        'the story? Support your answer with evidence from the text."'
    ),
    4: (
        "DOK 4 — Extended Thinking\n"
        "True DOK 4 usually means a multi-day project or work across several "
        "texts. This is a single-passage worksheet, so write an "
        "extended-response tier: each question should still require "
        "synthesizing information from the passage, then ask for a longer, "
        "more developed answer that connects the story to the student's own "
        "experience or the real world. The connection must be grounded in "
        "something the passage actually supports. Example shape: \"Have you "
        "ever felt nervous about doing something brave? How does your "
        "experience compare to Leo's?\""
    ),
}


MAX_WORKSHEET_QUESTIONS = 8


def parse_dok_level(raw) -> int:
    """Worksheet-wide question demand. Defaults to recall (DOK 1)."""
    if raw is None or raw == "":
        return 1
    if isinstance(raw, bool):
        return 1
    if isinstance(raw, int):
        return raw if raw in DOK_LEVELS else 1
    match = re.search(r"[1-4]", str(raw).strip())
    return int(match.group(0)) if match else 1


def parse_question_count(raw) -> int:
    """How many comprehension questions to write. Defaults to 5."""
    if raw is None or raw == "":
        return 5
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return 5
    if count < 1 or count > MAX_WORKSHEET_QUESTIONS:
        return 5
    return count


def parse_dok_levels(raw) -> list[int]:
    """One or more DOK levels. Custom mixes keep order and drop duplicates."""
    if isinstance(raw, (list, tuple, set)):
        levels = []
        for item in raw:
            level = parse_dok_level(item)
            if level not in levels:
                levels.append(level)
        return levels or [1]
    return [parse_dok_level(raw)]


def parse_dok_counts(raw, question_count) -> dict[int, int]:
    """Teacher-chosen counts per DOK type. Must sum to question_count."""
    count = parse_question_count(question_count)
    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Choose how many questions of each type.")
    for key, value in raw.items():
        level = parse_dok_level(key)
        try:
            n = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Choose how many questions of each type.") from exc
        if n < 0 or n > MAX_WORKSHEET_QUESTIONS:
            raise ValueError("Choose 0 to 8 questions of each type.")
        counts[level] += n
    if sum(counts.values()) != count:
        raise ValueError(f"Assign all {count} questions across the DOK types.")
    if not any(counts.values()):
        raise ValueError("Choose how many questions of each type.")
    return counts


def slots_from_counts(counts) -> list[int]:
    """Expand {1: 2, 3: 1} into [1, 1, 3]."""
    slots = []
    for level in (1, 2, 3, 4):
        slots.extend([level] * int((counts or {}).get(level, 0) or 0))
    return slots


def parse_dok_slots(raw, question_count) -> list[int]:
    """Ordered DOK level for each question. Length must match question_count."""
    count = parse_question_count(question_count)
    if not isinstance(raw, (list, tuple)) or len(raw) != count:
        raise ValueError(f"Choose a DOK level for each of the {count} questions.")
    slots = []
    for item in raw:
        if isinstance(item, bool) or item in (None, ""):
            raise ValueError("Choose a DOK level for each question.")
        if isinstance(item, int):
            level = item
        else:
            match = re.fullmatch(r"[1-4]", str(item).strip())
            level = int(match.group(0)) if match else None
        if level not in DOK_LEVELS:
            raise ValueError("Choose a DOK level for each question.")
        slots.append(level)
    return slots


def assign_dok_slots(
    question_count, dok_levels, dok_counts=None, dok_slots=None
) -> list[int]:
    """Map each question slot onto a DOK type.

    Prefer an explicit per-question order, then per-type counts, then cycle
    the selected types so the slot count always equals `question_count`.
    """
    count = parse_question_count(question_count)
    if dok_slots is not None:
        return parse_dok_slots(dok_slots, count)
    if dok_counts is not None:
        return slots_from_counts(parse_dok_counts(dok_counts, count))
    levels = parse_dok_levels(dok_levels)
    return [levels[index % len(levels)] for index in range(count)]


def _question_keys(count: int) -> list[str]:
    return [f"q{index}" for index in range(1, count + 1)]


def _worksheet_json_schema(question_count=5) -> dict:
    """Strict schema for a title, story, and q1..qn questions object."""
    keys = _question_keys(parse_question_count(question_count))
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "story": {"type": "string"},
            "questions": {
                "type": "object",
                "properties": {key: {"type": "string"} for key in keys},
                "required": keys,
                "additionalProperties": False,
            },
        },
        "required": ["title", "story", "questions"],
        "additionalProperties": False,
    }


def _questions_json_schema(question_count=5) -> dict:
    """Strict schema for a questions-only response."""
    keys = _question_keys(parse_question_count(question_count))
    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "object",
                "properties": {key: {"type": "string"} for key in keys},
                "required": keys,
                "additionalProperties": False,
            },
        },
        "required": ["questions"],
        "additionalProperties": False,
    }


def ordered_question_items(questions) -> list[tuple[str, str]]:
    """Stable q1, q2, … items with non-empty stems."""
    items = []
    for key, value in (questions or {}).items():
        match = re.fullmatch(r"q(\d+)", str(key), re.I)
        if not match:
            continue
        stem = str(value or "").strip()
        if stem:
            items.append((int(match.group(1)), stem))
    items.sort()
    return [(f"q{number}", stem) for number, stem in items]


def build_questions_prompt(
    story,
    title,
    dok_level,
    question_count=5,
    dok_levels=None,
    dok_counts=None,
    dok_slots=None,
):
    """Question-only prompt. The story is already written; do not rewrite it."""
    count = parse_question_count(question_count)
    slots = assign_dok_slots(
        count,
        dok_levels if dok_levels is not None else dok_level,
        dok_counts,
        dok_slots,
    )
    levels = []
    for level in slots:
        if level not in levels:
            levels.append(level)
    heading = title.strip() if title else "Untitled"
    keys_json = ",\n".join(f'    "{key}": "...?"' for key in _question_keys(count))
    if len(levels) == 1:
        level = levels[0]
        demand = (
            f"Write exactly {count} questions about the story below at DOK "
            f"level {level}.\n\n"
            f"{DOK_INSTRUCTIONS[level]}\n\n"
        )
        stem_rule = (
            "Choose whatever stems fit this DOK level and this story."
        )
    else:
        slot_lines = "\n".join(
            f"- {key}: DOK {level}"
            for key, level in zip(_question_keys(count), slots)
        )
        counts = {level: slots.count(level) for level in levels}
        mix = ", ".join(
            f"{counts[level]} at DOK {level}" for level in levels if counts[level]
        )
        demand = (
            f"Write exactly {count} questions about the story below.\n"
            f"Use this exact mix ({mix}). Do not add or drop questions.\n"
            f"Assign each question this DOK level:\n{slot_lines}\n\n"
            + "\n\n".join(DOK_INSTRUCTIONS[level] for level in levels)
            + "\n\n"
        )
        stem_rule = (
            "Choose whatever stems fit that question's assigned DOK level "
            "and this story."
        )
    return (
        "You are writing reading comprehension questions for a special "
        "education worksheet.\n\n"
        f"{demand}"
        "Rules:\n"
        f"- Write exactly {count} questions.\n"
        "- Write ordinary comprehension questions. Do not force a Who / What / "
        f"When / Where / Why template. {stem_rule}\n"
        "- Each question must end with a question mark.\n"
        "- Do not retell, rewrite, or continue the story.\n"
        "- Do not require facts the story does not support.\n"
        "- The story's reading level is already set. Do not make the questions "
        "easier or harder by changing the story; only the questions change.\n\n"
        f"Story title: {heading}\n\n"
        f"Story:\n{story}\n\n"
        "Respond with ONLY valid JSON (no markdown, no extra text) in this "
        "exact shape:\n"
        "{\n"
        '  "questions": {\n'
        f"{keys_json}\n"
        "  }\n"
        "}"
    )


def parse_questions_output(raw_text, question_count=5) -> dict:
    """Accept a questions object, or a full worksheet JSON that contains one."""
    count = parse_question_count(question_count)
    keys = _question_keys(count)
    if not raw_text or not str(raw_text).strip():
        raise ValueError("The writing assistant returned an empty set of questions. Try generating again.")
    text = str(raw_text).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    questions = None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            questions = data.get("questions")
    except json.JSONDecodeError:
        questions = None
    if not isinstance(questions, dict):
        questions = {}
        for key in keys:
            match = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.DOTALL)
            if not match:
                raise ValueError("The writing assistant's questions could not be read. Try generating again.")
            questions[key] = json.loads(f'"{match.group(1)}"')
    cleaned = {}
    for key in keys:
        stem = str(questions.get(key) or "").strip()
        if not stem:
            raise ValueError("The writing assistant left out a comprehension question. Try generating again.")
        if not stem.endswith("?"):
            stem = stem.rstrip(".!") + "?"
        cleaned[key] = stem
    return cleaned


def _log_dok_answerability(level: int):
    """There is no span-recovery verifier for worksheet questions.

    The only existing 'answerability' language is a line in the story prompt
    and readability scoring of stems on the leveled-passage API. A verbatim
    span check is not implemented, and would misfire on DOK 2–4. This log
    makes the skip visible so it cannot look like a pass.
    """
    if level == 1:
        _log_stage(
            "dok_answerability",
            dok_level=1,
            check="prompt_only",
            detail=(
                "No span-recovery verifier exists. DOK 1 keeps the prompt "
                "requirement that the answer is a locatable fact in the story."
            ),
        )
        return
    _log_stage(
        "dok_answerability",
        dok_level=level,
        check="skipped",
        detail=(
            "Answerability is skipped for DOK 2+ in this experimental pass. "
            "A span-style check would reject valid synthesis or inference."
        ),
    )


def generate_worksheet_questions(
    story,
    title,
    dok_level,
    question_count=5,
    dok_levels=None,
    dok_counts=None,
    dok_slots=None,
):
    """Second model call: questions only, at the requested DOK level(s)."""
    count = parse_question_count(question_count)
    slots = assign_dok_slots(
        count,
        dok_levels if dok_levels is not None else dok_level,
        dok_counts,
        dok_slots,
    )
    levels = []
    for level in slots:
        if level not in levels:
            levels.append(level)
    prompt = build_questions_prompt(
        story,
        title,
        levels[0],
        question_count=count,
        dok_levels=levels,
        dok_counts=dok_counts,
        dok_slots=slots,
    )
    data = generate_json(
        prompt,
        _questions_json_schema(count),
        name="worksheet_questions",
        system=(
            "You write reading comprehension questions for special education "
            "worksheets. Return only JSON that matches the required schema."
        ),
    )
    questions = parse_questions_output(json.dumps(data), count)
    for level in levels:
        _log_dok_answerability(level)
    return questions


def build_worksheet_user_prompt(
    grade, reading_level, interests, focus_vocabulary=None, focus_phonics=None
):
    """User half of the worksheet request. System instructions stay separate."""
    user_prompt = (
        f"I have a student who is in {grade} grade and reads at a "
        f"{reading_level} grade level. {interests} "
        f"Keep the story under {STORY_MAX_CHARS} characters so the worksheet "
        f"fits on one page."
    )
    terms = list(focus_vocabulary or [])
    if terms:
        quoted = ", ".join(f'"{term}"' for term in terms)
        user_prompt += (
            f" Focus vocabulary: {quoted}. Use each of these exact words "
            f"or short phrases 3 to 5 times in the story. Keep the spelling "
            f"exactly. Do not swap in an easier synonym."
        )
    phonics = parse_focus_phonics(focus_phonics)
    if phonics:
        user_prompt += (
            f' Focus phonics: "{phonics}". Include 3 to 5 different words '
            f"that contain this exact letter pattern. Use those words "
            f"naturally so the reader practices the pattern. Do not avoid "
            f"a word just because it contains the pattern."
        )
    user_prompt += format_level_vocab_for_prompt(reading_level)
    return user_prompt


def build_worksheet_prompt(
    grade, reading_level, interests, focus_vocabulary=None, focus_phonics=None
):
    user_prompt = build_worksheet_user_prompt(
        grade,
        reading_level,
        interests,
        focus_vocabulary=focus_vocabulary,
        focus_phonics=focus_phonics,
    )
    return f"{WORKSHEET_SYSTEM_PROMPT}\n\nUser request:\n{user_prompt}"


def validate_structure(data):
    required = ["title", "story", "questions"]

    for field in required:
        if field not in data:
            raise ValueError("The writing assistant left out part of the worksheet. Try generating again.")

    if not ordered_question_items(data.get("questions")):
        raise ValueError("The writing assistant left out a comprehension question. Try generating again.")

    return True


def _clip_chars(text, limit):
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    clipped = value[: max(0, limit - 1)].rstrip(" ,;:-")
    return f"{clipped}…"


def normalize_model_output(data):
    payload = {
        "Title": _clip_chars(data["title"], TITLE_MAX_CHARS),
        "Story": _clip_chars(data["story"], STORY_MAX_CHARS),
    }
    for index, (_key, stem) in enumerate(ordered_question_items(data.get("questions")), start=1):
        payload[f"Q{index}"] = _clip_chars(stem, QUESTION_MAX_CHARS)
    return payload


def parse_worksheet_output(raw_text):
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        extracted = _extract_json_fields(text)
        if extracted:
            return extracted
        return _parse_worksheet_text(text)


def _extract_json_fields(text):
    title_match = re.search(r'"title"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.DOTALL)
    story_match = re.search(r'"story"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.DOTALL)
    if not title_match or not story_match:
        return None

    questions = {}
    for key in ["q1", "q2", "q3", "q4", "q5"]:
        match = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.DOTALL)
        if not match:
            return None
        questions[key] = json.loads(f'"{match.group(1)}"')

    return {
        "title": json.loads(f'"{title_match.group(1)}"'),
        "story": json.loads(f'"{story_match.group(1)}"'),
        "questions": questions,
    }


def _parse_worksheet_text(output_text):
    """Fallback parser preserved from the former backend."""
    output_text = output_text.replace("*", "")
    lines = [line.strip() for line in output_text.splitlines() if line.strip()]

    if len(lines) < 7:
        raise ValueError("The writing assistant's worksheet could not be read. Try generating again.")

    title = lines[0]
    question_indices = [i for i, line in enumerate(lines) if line.endswith("?")]
    if len(question_indices) < 5:
        raise ValueError("The writing assistant did not return five questions. Try generating again.")

    last_five_q_indices = question_indices[-5:]
    questions = [lines[i] for i in last_five_q_indices]
    story_lines = lines[1:last_five_q_indices[0]]
    story = "\n\n".join(story_lines)

    return {
        "title": title,
        "story": story,
        "questions": {
            "q1": questions[0],
            "q2": questions[1],
            "q3": questions[2],
            "q4": questions[3],
            "q5": questions[4],
        },
    }


def generate_worksheet_content(
    grade, reading_level, interests, focus_vocabulary=None, focus_phonics=None
):
    prompt = build_worksheet_user_prompt(
        grade,
        reading_level,
        interests,
        focus_vocabulary=focus_vocabulary,
        focus_phonics=focus_phonics,
    )
    try:
        model_data = generate_json(
            prompt,
            _worksheet_json_schema(5),
            name="worksheet",
            system=WORKSHEET_SYSTEM_PROMPT,
        )
    except RuntimeError:
        # Rare: model ignored the schema. Try a loose parse before failing.
        raise
    validate_structure(model_data)
    return model_data, json.dumps(model_data)


def build_pdf_bytes(
    worksheet_content,
    image_feedback=None,
    existing_image_base64=None,
    grade=None,
    *,
    include_image=True,
):
    pdf_json = normalize_model_output(worksheet_content)
    student_grade = grade if grade is not None else worksheet_content.get("grade")
    image_base64 = None

    if include_image:
        if image_feedback:
            image_prompt = create_image_prompt(
                pdf_json["Title"],
                pdf_json["Story"],
                feedback=image_feedback,
                grade=student_grade,
            )
            image_base64 = generate_image(image_prompt)
        elif existing_image_base64:
            image_base64 = existing_image_base64
        else:
            image_prompt = create_image_prompt(
                pdf_json["Title"], pdf_json["Story"], grade=student_grade
            )
            image_base64 = generate_image(image_prompt)

    if image_base64:
        pdf_json["image"] = f"data:image/jpeg;base64,{image_base64}"
    pdf_bytes = file.generate_worksheet_pdf(pdf_json)
    return pdf_bytes, image_base64


def generate_full_worksheet(
    grade,
    reading_level,
    interests,
    focus_vocabulary=None,
    focus_phonics=None,
    dok_level=None,
    question_count=None,
    dok_levels=None,
    dok_counts=None,
    dok_slots=None,
    include_image=True,
):
    """Generate a worksheet, then level the story with the simple pipeline.

    First draft is kept for the review panel. PDF uses the leveled story.
    A leveling miss still ships the closest text, flagged. Focus vocabulary
    and phonics matches are protected so the rewriter cannot drop them.
    Questions are a second call so DOK changes cannot rewrite the story.

    When include_image is True but Flux fails, the worksheet still ships
    without a picture and image_failed is set so the UI can ask what to do.
    """
    terms = parse_focus_vocabulary(focus_vocabulary)
    phonics = parse_focus_phonics(focus_phonics)
    model_data, raw_text = generate_worksheet_content(
        grade,
        reading_level,
        interests,
        focus_vocabulary=terms,
        focus_phonics=phonics,
    )
    model_data["grade"] = grade
    original_story = model_data["story"]
    protected = _unique_protected_terms(
        terms, words_matching_phonics(original_story, phonics)
    )

    try:
        outcome = level_story(
            original_story,
            reading_level,
            protected_terms=protected,
            allow_rewrite=True,
            generate=generate_text,
            passage_type="narrative",
        )
        model_data["story"] = outcome.text
        leveling = leveling_report_for_ui(original_story, outcome)
    except (ReadingLevelError, ValueError):
        logger.exception("Reading-level pipeline failed; shipping the first draft")
        first_score = score_text(original_story)
        try:
            fallback_band = rl_score.target_band(reading_level)
            target_display = fallback_band.display
            below_target = first_score.estimated_grade < fallback_band.low
        except ValueError:
            target_display = str(reading_level)
            below_target = False
        leveling = {
            "target_band": target_display,
            "first_draft": {
                "estimated_grade": round(first_score.estimated_grade, 2),
                "confidence": first_score.confidence,
                "in_band": False,
                "below_target": below_target,
                "story": original_story,
            },
            "final": {
                "estimated_grade": round(first_score.estimated_grade, 2),
                "confidence": first_score.confidence,
                "in_band": False,
            },
            "improved": False,
            "llm_rewrite_applied": False,
        }

    count = parse_question_count(question_count)
    slots = assign_dok_slots(
        count,
        dok_levels if dok_levels is not None else dok_level,
        dok_counts,
        dok_slots,
    )
    levels = []
    for level in slots:
        if level not in levels:
            levels.append(level)
    model_data["questions"] = generate_worksheet_questions(
        model_data["story"],
        model_data.get("title", ""),
        levels[0],
        question_count=count,
        dok_levels=levels,
        dok_counts=dok_counts,
        dok_slots=slots,
    )
    model_data["dok_level"] = levels[0] if len(levels) == 1 else None
    model_data["dok_levels"] = levels
    model_data["dok_slots"] = slots
    model_data["dok_counts"] = {str(level): slots.count(level) for level in levels}
    model_data["question_count"] = count

    image_failed = False
    image_error = None
    if include_image:
        try:
            pdf_bytes, image_base64 = build_pdf_bytes(
                model_data, grade=grade, include_image=True
            )
        except Exception as exc:
            logger.exception("Illustration failed; building PDF without an image")
            image_failed = True
            image_error = user_error_message(exc, "flux")
            pdf_bytes, image_base64 = build_pdf_bytes(
                model_data, grade=grade, include_image=False
            )
    else:
        pdf_bytes, image_base64 = build_pdf_bytes(
            model_data, grade=grade, include_image=False
        )

    return (
        model_data,
        pdf_bytes,
        image_base64,
        raw_text,
        leveling,
        image_failed,
        image_error,
    )


def _log_stage(stage, **fields):
    """Emit a parseable log line.

    These records are the dataset for future calibration and fine-tuning:
    every passage that needed several iterations is a training example, so the
    payload is JSON rather than prose.
    """
    logger.info(json.dumps({"stage": stage, **fields}, default=str))


def _score_summary(score):
    return {
        "estimated_grade": round(score.estimated_grade, 2),
        "confidence": score.confidence,
        "flags": list(score.flags),
    }


def build_leveled_prompt(topic, band, student_interest=None, feedback=None):
    """Prompt for a passage aimed at a band.

    The band is included because it improves first-draft quality, but it is
    only a hint: the scorer, not the model, decides whether the draft counts.
    """
    request = (
        f"Write the story about: {topic}. "
        f"Target reading level: {band.display} "
        f"(roughly grade {band.center:.0f})."
    )
    if student_interest:
        request += f" The student is interested in {student_interest}."
    if feedback:
        request += f" The previous attempt was rejected because {feedback}."

    return f"{WORKSHEET_SYSTEM_PROMPT}\n\nUser request:\n{request}"


def _difficulty_feedback(result):
    """Short note for a regenerate retry when leveling still misses."""
    if getattr(result, "failure_reason", None) == "below_target_band" or (
        getattr(result, "final_score", None)
        and result.target_band
        and result.final_score.estimated_grade < result.target_band.low
    ):
        return (
            "the passage read below the target grade band "
            "(use more specific words and slightly longer sentences)"
        )
    return "the passage read above the target grade band (use shorter, more common words)"


def _level_questions(questions, band):
    """Score question stems against a widened band.

    A single stem is far below the length at which readability statistics mean
    anything, so these are measured and logged but never hard-gated.
    """
    tolerant_band = rl_score.widen(band, rl_score.QUESTION_BAND_TOLERANCE)
    report = {}

    for key, stem in questions.items():
        score = score_text(stem)
        report[key] = {
            **_score_summary(score),
            "in_tolerant_band": rl_score.in_band(score, tolerant_band),
        }

    _log_stage(
        "questions_scored",
        band=band.label,
        tolerance=rl_score.QUESTION_BAND_TOLERANCE,
        questions=report,
    )
    return report


def _leveled_response(model_data, band, result, protected_terms, rewritten=False):
    """Shape the API payload for an accepted passage."""
    model_data["story"] = result.text
    questions = _level_questions(model_data["questions"], band)
    lowered = result.text.lower()

    return {
        "title": model_data["title"],
        "passage": result.text,
        "questions": model_data["questions"],
        "question_levels": questions,
        "reading_level": {
            "estimated_grade": round(result.final_score.estimated_grade, 2),
            "target_band": band.display,
            "in_band": result.gate_passed,
            "confidence": result.final_score.confidence,
        },
        "corrections_applied": len(getattr(result, "edits", []) or []),
        "protected_terms_preserved": [
            term for term in protected_terms if term.lower() in lowered
        ],
        "llm_rewrite_applied": rewritten,
    }


def correct_with_rewrite(
    text,
    target_grade,
    *,
    protected_terms=None,
    allow_rewrite=False,
    passage_type=None,
):
    """Score a passage; optionally one AI rewrite if it is outside the band.

    Never raises on a band miss — returns the best text with gate_passed=False
    so a teacher can review it.
    """
    return level_story(
        text,
        target_grade,
        protected_terms=protected_terms,
        allow_rewrite=allow_rewrite,
        generate=generate_text if allow_rewrite else None,
        passage_type=passage_type,
    )


def generate_leveled_passage(
    topic,
    target_grade,
    *,
    protected_terms=None,
    student_interest=None,
    passage_type="narrative",
):
    """Generate a passage and require it to land in the target band.

    Raises ReadingLevelError if the draft + one rewrite still miss.
    """
    band = rl_score.target_band(target_grade)
    protected_terms = list(protected_terms or [])

    prompt = build_leveled_prompt(topic, band, student_interest, feedback=None)
    # Nudge the first draft with level vocabulary (same list the rewriter uses).
    prompt = prompt + format_level_vocab_for_prompt(target_grade)
    model_data = generate_json(
        prompt,
        _worksheet_json_schema(5),
        name="leveled_passage",
    )
    validate_structure(model_data)

    outcome = level_story(
        model_data["story"],
        target_grade,
        protected_terms=protected_terms,
        allow_rewrite=True,
        generate=generate_text,
        passage_type=passage_type,
    )
    _log_stage(
        "leveled",
        band=band.label,
        in_band=outcome.gate_passed,
        llm_rewrite_applied=outcome.llm_rewrite_applied,
        final_score=_score_summary(outcome.final_score),
    )
    if outcome.gate_passed:
        return _leveled_response(
            model_data,
            band,
            outcome,
            protected_terms,
            rewritten=outcome.llm_rewrite_applied,
        )

    raise ReadingLevelError(
        f"Could not bring the passage into {band.display}.",
        reason=outcome.failure_reason,
        score=outcome.final_score,
        band=band,
    )


_BLOCK_CLASSIFY_PROMPT = """You assign a type to one excerpt from a student worksheet.
Do not rewrite, summarize, or add text. Classify only.

Allowed types: passage, instructions, question, answer_choice, ignore

Use ignore for labels and fields the student fills in (Name, Date, Score)
and for anything that should not be rewritten.

Excerpt:
{text}

Respond with ONLY valid JSON: {{"block_type": "passage"}}"""


def classify_unclassified_blocks(blocks, *, generate=None):
    """Kept for callers; ignore/image blocks are never sent to the model."""
    generate = generate or generate_text
    updated = []
    for block in blocks:
        if block.block_type != "ignore" or block.confidence == "high":
            updated.append(block)
            continue
        if block.asset_base64:
            updated.append(block)
            continue
        raw = generate(_BLOCK_CLASSIFY_PROMPT.format(text=block.text))
        assigned = _parse_block_type(raw)
        updated.append(
            WorksheetBlock(
                block_id=block.block_id,
                block_type=assigned,
                text=block.text,
                source_position=block.source_position,
                confidence="low",
                asset_base64=block.asset_base64,
                asset_mime=block.asset_mime,
            )
        )
    return updated


def _parse_block_type(raw_text) -> str:
    text = str(raw_text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        value = str(data.get("block_type") or "").strip()
    except (json.JSONDecodeError, AttributeError):
        value = ""
        match = re.search(
            r"passage|instructions|question|answer_choice|ignore",
            text,
        )
        if match:
            value = match.group(0)
    if value == "unclassified":
        value = "ignore"
    return value if value in BLOCK_TYPES else "ignore"


def segment_worksheet(data: bytes, filename: str, *, generate=None, use_llm=False):
    """Split a worksheet. Leftover labels default to ignore; the teacher confirms.

    `use_llm` is off by default: confirmation is the checkpoint, not a second
    guess from the model. Image blocks always pass through unchanged.
    """
    blocks = segment_worksheet_bytes(data, filename)
    if use_llm and any(
        block.block_type == "ignore" and block.confidence == "low"
        for block in blocks
    ):
        blocks = classify_unclassified_blocks(blocks, generate=generate)
    return blocks


def build_convert_question_prompt(
    stem, target_dok, passage, source_material=None, protected_terms=None
):
    """Rewrite an existing stem to a target DOK. Reuses generation definitions."""
    level = parse_dok_level(target_dok)
    protected = ", ".join(f'"{term}"' for term in (protected_terms or []) if term)
    source = (source_material or "").strip()
    return (
        "You are converting an existing reading-comprehension question to a "
        f"different Depth of Knowledge level. Target: DOK {level}.\n\n"
        f"{DOK_INSTRUCTIONS[level]}\n\n"
        "Keep the same underlying fact or idea as the original question. "
        "Do not invent a new topic. Do not rewrite the passage. "
        "Write ordinary comprehension questions — do not force a "
        "Who / What / When / Where / Why template.\n"
        "Ground the question in the passage"
        + (" and the source material" if source else "")
        + ". Do not require facts that neither one supports.\n"
        + (f"Protected terms, keep verbatim: {protected}\n" if protected else "")
        + f"\nOriginal question:\n{stem}\n\nPassage:\n{passage}\n"
        + (f"\nSource material:\n{source}\n" if source else "")
        + "\nRespond with ONLY valid JSON: {\"question\": \"...?\"}"
    )


def convert_question_to_dok(
    stem,
    target_dok,
    passage,
    source_material=None,
    protected_terms=None,
    *,
    generate=None,
):
    generate = generate or generate_text
    prompt = build_convert_question_prompt(
        stem, target_dok, passage, source_material, protected_terms
    )
    raw = generate(prompt)
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        question = str(data.get("question") or "").strip()
    except json.JSONDecodeError:
        question = ""
        match = re.search(r'"question"\s*:\s*"((?:\\.|[^"\\])*)"', text)
        if match:
            question = json.loads(f'"{match.group(1)}"').strip()
    if not question:
        raise ValueError("The writing assistant did not return a rewritten question. Try again.")
    if not question.endswith("?"):
        question = question.rstrip(".!") + "?"
    return question


def modulate_worksheet(
    blocks,
    target_grade,
    *,
    source_material="",
    worksheet_kind="practice",
    dok_mix=None,
    protected_terms=None,
    allow_rewrite=True,
    passage_type=None,
    convert_questions=False,
    generate=None,
    title="Worksheet",
):
    """Level passages and choices. Questions stay put unless asked.

    Convert is off by default: the teacher keeps the original stems.
    When `convert_questions` is on, stems in the wrong DOK bucket are
    rewritten toward the mix (convert, not replace).
    """
    generate = generate or generate_text
    protected = list(protected_terms or [])
    kind = (worksheet_kind or "practice").strip().lower()
    required_source = kind == "test"
    source_tokens = rl_dok.check_source_material(
        source_material, required=required_source
    )

    working = []
    for block in blocks:
        item = block if isinstance(block, WorksheetBlock) else WorksheetBlock.from_dict(block)
        if item.block_type == "image" and item.asset_base64:
            working.append(item)
        elif item.text:
            working.append(item)

    passage_report = []
    leveled = []
    for block in working:
        if block.block_type == "passage":
            outcome = correct_with_rewrite(
                block.text,
                target_grade,
                protected_terms=protected,
                allow_rewrite=allow_rewrite,
                passage_type=passage_type,
            )
            leveled.append(
                WorksheetBlock(
                    block_id=block.block_id,
                    block_type=block.block_type,
                    text=outcome.text,
                    source_position=block.source_position,
                    confidence=block.confidence,
                )
            )
            passage_report.append({
                "block_id": block.block_id,
                "initial_grade": round(outcome.correction.initial_score.estimated_grade, 2),
                "final_grade": round(outcome.final_score.estimated_grade, 2),
                "in_band": outcome.gate_passed,
                "target_band": outcome.target_band.display,
                "llm_rewrite_applied": outcome.llm_rewrite_applied,
            })
        elif block.block_type == "answer_choice":
            result = correct_text(
                block.text, target_grade, protected_terms=protected
            )
            _, label_text = rl_dok.choice_label_and_text(block.text)
            leveled_body = result.text.strip()
            label, _ = rl_dok.choice_label_and_text(block.text)
            if label and not re.match(r"^[A-D][.)]", leveled_body):
                leveled_body = f"{label}. {leveled_body}"
            elif not label:
                leveled_body = leveled_body or label_text
            leveled.append(
                WorksheetBlock(
                    block_id=block.block_id,
                    block_type=block.block_type,
                    text=leveled_body,
                    source_position=block.source_position,
                    confidence=block.confidence,
                )
            )
        else:
            leveled.append(block)

    passage_text = " ".join(
        block.text for block in leveled if block.block_type == "passage"
    )
    corpora = (passage_text, source_material or "")
    items = rl_dok.group_question_items(leveled)
    before = rl_dok.bucket_questions(items, *corpora)
    percents = dok_mix or rl_dok.default_dok_mix(target_grade)
    target_counts = rl_dok.mix_to_counts(len(items), percents)
    targets = rl_dok.target_dok_for_items(before, target_counts)

    converted_ids = []
    by_id = {block.block_id: block for block in leveled}
    if convert_questions:
        for item, diagnostic, target in zip(items, before, targets):
            if diagnostic.estimated_dok == target:
                continue
            try:
                new_stem = convert_question_to_dok(
                    item.question.text,
                    target,
                    passage_text,
                    source_material=source_material,
                    protected_terms=protected,
                    generate=generate,
                )
            except Exception:
                logger.exception(
                    "question_convert_failed", extra={"block_id": item.question.block_id}
                )
                continue
            old = item.question
            by_id[old.block_id] = WorksheetBlock(
                block_id=old.block_id,
                block_type=old.block_type,
                text=new_stem,
                source_position=old.source_position,
                confidence=old.confidence,
            )
            converted_ids.append(old.block_id)
        leveled = [by_id[block.block_id] for block in leveled]

    items = rl_dok.group_question_items(leveled)
    after = rl_dok.bucket_questions(items, *corpora)
    after_levels = [item.estimated_dok for item in after]
    achieved = rl_dok.mix_from_levels(after_levels)
    _log_stage(
        "dok_mix",
        source_tokens=source_tokens,
        before={row.estimated_dok: None for row in before},
        before_counts=rl_dok.mix_from_levels([row.estimated_dok for row in before]),
        target_counts=target_counts,
        achieved_counts=achieved,
        converted_block_ids=converted_ids,
        convert_questions=convert_questions,
    )

    gate_failures = []
    for row in passage_report:
        if not row["in_band"]:
            gate_failures.append({
                "check": "passage_band",
                "block_id": row["block_id"],
                "detail": (
                    f"Passage measured grade {row['final_grade']}, "
                    f"outside {row['target_band']}."
                ),
            })

    lowered_passages = passage_text.lower()
    for term in protected:
        if term.lower() not in lowered_passages:
            gate_failures.append({
                "check": "protected_terms",
                "block_id": None,
                "detail": f'Protected term "{term}" is missing from the passage.',
            })

    if convert_questions and not rl_dok.mix_within_tolerance(
        achieved, target_counts, len(items)
    ):
        gate_failures.append({
            "check": "dok_mix",
            "block_id": None,
            "detail": f"DOK mix {achieved} is outside tolerance of {target_counts}.",
        })

    if convert_questions:
        for item, diagnostic, target in zip(items, after, targets):
            if diagnostic.unanswerable and target <= 2:
                gate_failures.append({
                    "check": "answerability",
                    "block_id": item.question.block_id,
                    "detail": "No locatable overlap with the passage or source material.",
                })
            if item.choices:
                for problem in rl_dok.check_distractors(item.choices, *corpora):
                    gate_failures.append({
                        "check": "distractors",
                        "block_id": item.question.block_id,
                        "detail": problem,
                    })

    worksheet = file.blocks_to_worksheet_data(title, leveled)
    pdf_bytes = file.generate_blocks_pdf(title, leveled)
    return {
        "title": (worksheet or {}).get("Title") or title,
        "worksheet": worksheet,
        "blocks": [block.to_dict() for block in leveled],
        "pdf_base64": base64.b64encode(pdf_bytes).decode(),
        "source_tokens": source_tokens,
        "passages": passage_report,
        "dok": {
            "before": [row.to_dict() for row in before],
            "after": [row.to_dict() for row in after],
            "before_counts": rl_dok.mix_from_levels([row.estimated_dok for row in before]),
            "target_counts": target_counts,
            "achieved_counts": achieved,
            "converted_block_ids": converted_ids,
            "percents": percents,
        },
        "protected_terms_preserved": [
            term for term in protected if term.lower() in lowered_passages
        ],
        "gate_passed": not gate_failures,
        "gate_failures": gate_failures,
    }


if __name__ == "__main__":
    content, _ = generate_worksheet_content(
        grade="6",
        reading_level="1st",
        interests="my student is interested in watercolor paintings",
    )
    print(json.dumps(content, indent=2))
