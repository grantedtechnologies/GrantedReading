"""Operator D: a diagnostic-driven LLM rewrite, used only when repair fails.

Operators A, B, and C are fast, free, and auditable. This one is none of those,
so it runs only on passages the deterministic path could not bring into band --
never as the default.

It lives outside reading_level/ because it makes a network call. The package
builds the diagnosis (reading_level.diagnostics); this module constructs the
prompt and carries the answer back to the scorer. The deterministic scorer
stays the single source of truth: the model is never asked what grade the
output is, and is never allowed to report one. Meaning-drift verification
runs after every rewrite in single_pass_llm; the iterative escalation path
still gates on score_text landing in band.
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field

from reading_level import bands as rl_bands
from reading_level import build_diagnostics, score_text
from reading_level import config as rl_config
from reading_level.diagnostics import PassageDiagnostics
from reading_level.repair import CorrectionResult
from reading_level.verify import check_semantic_drift

logger = logging.getLogger(__name__)

REWRITE_ROLE = """You are a reading-level editor for a special education teacher.

A deterministic pass already measured this passage against a target grade. Your job is to bring the passage into the target band — down if it is too hard, up if it is too easy. The work list below says which lever to push and how hard. Do not regenerate the passage from scratch. Keep unflagged wording unless a listed edit or the sentence-length target requires a change."""

# What "easier" means, stated as a literary/pedagogical rule, not as "use a
# synonym." WordNet synonyms are how "postpone" became "table" and "motive"
# became "need": they share a thesaurus line and not a use in the sentence.
EASIER_WORD_DEFINITION = """What counts as an easier word (literary definition):
An easier word is one a typical reader at the target grade already owns — high-frequency, concrete, and in everyday use — that can stand in the same grammatical slot and still assert the same thing.

Prefer:
- Common spoken English over formal or Latinate diction (help, not facilitate; start, not commence; show, not demonstrate; delay, not postpone).
- Concrete words over abstractions when the sentence is about a specific act or thing (house, not residence; try, not endeavor).
- Short, familiar stems over stacked suffixes (use, not utilization).
- The same part of speech, taking the same subject and object.

Never do this:
- Baby talk, slang, or cutesy stand-ins.
- A thesaurus cousin that only looks simpler. "Table" is not an easier "postpone" in a business sentence; "need" is not an easier "motive"; "fleet" is not an easier "swift." If a one-word swap would change the claim, rewrite the phrase in plain English instead.
- A word the lesson is teaching. Those are listed as protected terms and stay verbatim.

If no single common word carries the meaning, do not force a swap. Recast the clause: "exacerbate an unstable situation" can become "make a shaky situation worse.\""""

HARDER_WORD_DEFINITION = """What counts as a harder word (literary definition):
A harder word is a more specific, grade-appropriate content word that still asserts the same claim — not a rare technical term and not a thesaurus stunt.

Prefer:
- Precise nouns and verbs a typical reader at the target grade is learning (observe, not look; moisture, not wetness; harvest, not pick).
- The same part of speech, taking the same subject and object.

Never do this:
- Jargon, Latinate stacking, or words the lesson is not teaching.
- New facts, examples, or conclusions added only to sound older.
- Protected terms swapped for a harder synonym."""

SENTENCE_SHORTENING = """What counts as a shorter sentence:
The scorer reads the AVERAGE number of words per sentence, not only the longest one. Split at a natural break (and, but, so, or, or after a finished thought) so each new sentence has its own subject and finite verb. Do not delete facts or plot to get under the limit. A fragment is worse than a long sentence."""

SENTENCE_LENGTHENING = """What counts as a longer sentence:
The scorer reads the AVERAGE number of words per sentence. Combine two short sentences that belong together, or add a dependent clause that was already implied, so the average rises toward the target. Do not add empty filler. No sentence may go over the word ceiling."""

NARRATIVE_FREEDOM = """This is a NARRATIVE passage.
- Apply the listed word and sentence edits first.
- Keep the same theme and topic.
- If a listed edit cannot land without a small structural change, you may adjust the surrounding clause, the order of events, or even the ending. Do not rewrite scenes that were not flagged.
- Never invert a fact or flip a negation mid-passage. A changed ending is allowed only as a deliberate restructure, and every sentence that refers to it must agree."""

INFORMATIONAL_FREEDOM = """This is an INFORMATIONAL passage.
- Apply the listed word and sentence edits first.
- Keep every fact the passage conveys. Comprehension questions are built on them.
- Wording and sentence breaks may change; the facts themselves may not.
- Never invert a fact or flip a negation."""

SCORER_LEVERS = """How the grade is measured — these are the only two numbers that move it:
1. How common the content words are. Rare nouns, verbs, adjectives, and adverbs pull the grade up. Function words (the, of, and, that) do not count. Replacing a listed hard word with a common one is the vocabulary lever.
2. How long the sentences are. Mean words per sentence is the syntax lever. Lengthen to raise the grade, shorten to lower it.

A rewrite that still uses the rare topic words and long sentences will still measure near the original grade. A rewrite that swaps every slightly uncommon word and chops every sentence into six words will undershoot the band. Protected terms are the exception: leave those, and spend the difficulty budget on everything else."""

AIM_FOR_BAND = """Land inside the target band. Going below the floor is as wrong as staying above the ceiling. Aim near the middle of the band, not the edge — a draft sitting on the ceiling is a miss.

Do not flatten claims into vague filler ("felt that way," "money problem") just to make a word easier."""

REWRITE_INVARIANTS = """These hold no matter which kind of passage this is:
- Never invert a fact or flip a negation. If you change what happens, change every sentence that refers to it so the passage stays consistent with itself.
- Keep every protected term exactly as written, wherever it appears. Do not paraphrase one away or substitute an easier synonym for it, however difficult the term is.
- Do not add new information, examples, or conclusions."""

REWRITE_OUTPUT_FORMAT = """Respond with ONLY valid JSON (no markdown, no extra text) in this exact shape:
{"passage": "The rewritten passage as a single paragraph."}
Do not include a grade, reading level, estimated_grade, or any claim about how hard the passage is. Return the passage text only."""

_FREEDOM_BY_TYPE = {
    rl_config.PASSAGE_TYPE_NARRATIVE: NARRATIVE_FREEDOM,
    rl_config.PASSAGE_TYPE_INFORMATIONAL: INFORMATIONAL_FREEDOM,
}


@dataclass
class RewriteAttempt:
    """One model draft and what the scorer said about it."""

    attempt: int
    text: str
    estimated_grade: float
    in_band: bool


@dataclass
class RewriteOutcome:
    """What Operator D produced, and whether it was allowed to ship."""

    text: str
    correction: CorrectionResult
    final_score: object
    gate_passed: bool
    failure_reason: str | None = None
    llm_rewrite_applied: bool = False
    llm_attempts: int = 0
    naturalness: dict | None = None
    rewrite_attempts: list = field(default_factory=list)
    drift_report: object | None = None

    # The deterministic record stays reachable unchanged: the rewrite pass adds
    # to the story of a correction, it does not replace it.
    @property
    def edits(self):
        return self.correction.edits

    @property
    def score_trajectory(self):
        return self.correction.score_trajectory

    @property
    def initial_score(self):
        return self.correction.initial_score

    @property
    def target_band(self):
        return self.correction.target_band


def _log(stage, **fields):
    logger.info(json.dumps({"stage": stage, **fields}, default=str))


# --------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------


# Words below this average are fragments, not grade-appropriate prose.
_MIN_TARGET_MEAN_LENGTH = 6.0
# If the current average is this many words above the target, syntax is the
# leftover lever and the model must split more than the listed long sentences.
_SYNTAX_DEFICIT_WORDS = 2.0


def _mean_length_for_grade(grade: float, mean_log_word_freq: float) -> float:
    """Invert the scorer: sentence length that hits `grade` at this vocabulary."""
    return (
        grade
        - rl_config.GRADE_INTERCEPT
        - rl_config.GRADE_COEF_WORD_FREQ * mean_log_word_freq
    ) / rl_config.GRADE_COEF_SENTENCE_LENGTH


def _target_mean_sentence_length(diagnostics: PassageDiagnostics) -> float:
    """Average words/sentence that would land this vocabulary at band center.

    When many rare words are still on the list, expect some of the drop to
    come from swaps and do not demand the entire gap from syntax — that
    pairing is how a grade-6 rewrite overshot to 5.39.
    """
    needed = _mean_length_for_grade(
        diagnostics.target_band.center, diagnostics.mean_log_word_freq
    )
    hard_count = len(diagnostics.hard_words)
    if hard_count >= 8:
        needed += 0.5 / rl_config.GRADE_COEF_SENTENCE_LENGTH
    elif hard_count >= 3:
        needed += 0.25 / rl_config.GRADE_COEF_SENTENCE_LENGTH
    ceiling = float(diagnostics.sentence_limit)
    return min(max(needed, _MIN_TARGET_MEAN_LENGTH), ceiling)


def _syntax_deficit(diagnostics: PassageDiagnostics) -> float:
    return diagnostics.mean_sentence_length - _target_mean_sentence_length(diagnostics)


def _too_easy(diagnostics: PassageDiagnostics) -> bool:
    return diagnostics.current_grade < diagnostics.target_band.low


def _effort_plan(diagnostics: PassageDiagnostics) -> str:
    """Name the leftover lever with numbers, scaled to the size of the gap."""
    target_mean = _target_mean_sentence_length(diagnostics)
    current_mean = diagnostics.mean_sentence_length
    deficit = current_mean - target_mean
    band = diagnostics.target_band

    lines = [
        f"Sentences currently average {current_mean:.1f} words. "
        f"To land near grade {band.center:.1f} they need to average about "
        f"{target_mean:.1f} words (no sentence over {diagnostics.sentence_limit})."
    ]

    if _too_easy(diagnostics):
        if deficit <= -_SYNTAX_DEFICIT_WORDS:
            lines.append(
                "Sentence length is the main leftover lever. Combine short "
                "sentences or add a clause that was already implied so the "
                "average rises. Do not pad with empty filler."
            )
        else:
            lines.append(
                "Sentence length is already close. Prefer more specific "
                "content words over making sentences much longer."
            )
        lines.append(
            "Vocabulary needs to come up to the target grade. Use more "
            "specific, grade-appropriate content words. Keep the same claims."
        )
        return "\n".join(lines)

    if deficit >= _SYNTAX_DEFICIT_WORDS:
        lines.append(
            "Sentence length is the main leftover lever. Split listed long "
            "sentences AND other sentences that keep the average high, "
            "including ones that were copied unchanged last time. A 16-word "
            "sentence left intact will miss the band."
        )
    else:
        lines.append(
            "Sentence length is already close. Split only sentences over the "
            "ceiling. Do not chop every sentence into a very short one."
        )

    if diagnostics.hard_words and deficit < _SYNTAX_DEFICIT_WORDS:
        lines.append(
            "Vocabulary is the main leftover lever. Replace the rarest listed "
            "words. Leave mid-level words that already fit the grade."
        )
    elif diagnostics.hard_words:
        lines.append(
            "Vocabulary is secondary here. Only swap a listed word when it is "
            "truly rare; do not flatten every mid-level word."
        )

    return "\n".join(lines)


def _hard_words_heading(diagnostics: PassageDiagnostics) -> str:
    if _syntax_deficit(diagnostics) >= _SYNTAX_DEFICIT_WORDS:
        return (
            "\nHard words — only swap these when a word is truly rare. "
            "Do not flatten mid-level wording just to tick the list:\n"
        )
    return (
        "\nHard words — start with the rarest of these. Replace only as "
        "many as you need to reach the target band. Leave a listed word "
        "if a simpler swap would flatten the claim or drop the passage "
        "below the band:\n"
    )


def _describe_hard_words(diagnostics: PassageDiagnostics) -> str:
    lines = []
    for word in diagnostics.hard_words:
        line = f'- Replace "{word.word}"'
        if word.sentence:
            line += f' in: "{word.sentence}"'
        if word.suggested_replacements:
            options = ", ".join(f'"{o}"' for o in word.suggested_replacements)
            line += (
                f"\n  Algorithm-vetted options (use only if they fit the "
                f"literary definition above): {options}"
            )
        else:
            line += (
                "\n  No algorithm-vetted option. Pick a simpler word that "
                "keeps the claim, or recast the clause."
            )
        lines.append(line)
    return "\n".join(lines)


def _describe_long_sentences(diagnostics: PassageDiagnostics) -> str:
    limit = diagnostics.sentence_limit
    return "\n".join(
        f"- {sentence.word_count} words (limit {limit}): {sentence.text}"
        for sentence in diagnostics.long_sentences
    )


def build_rewrite_prompt(
    passage: str, diagnostics: PassageDiagnostics, feedback: str | None = None
) -> str:
    """Turn a diagnosis into a rewrite request.

    The diagnosis is the work list. The model is asked to apply those edits,
    not to invent a new passage. How much surrounding structure it may touch
    depends on `diagnostics.passage_type`.
    """
    band = diagnostics.target_band
    freedom = _FREEDOM_BY_TYPE[diagnostics.passage_type]
    raising = _too_easy(diagnostics)
    if raising:
        direction = (
            f"The passage currently reads at grade {diagnostics.current_grade:.1f}, "
            f"which is {abs(diagnostics.grade_gap):.1f} grade levels too low."
        )
        word_rule = HARDER_WORD_DEFINITION
        sentence_rule = SENTENCE_LENGTHENING
    else:
        direction = (
            f"The passage currently reads at grade {diagnostics.current_grade:.1f}, "
            f"which is {diagnostics.grade_gap:.1f} grade levels too high."
        )
        word_rule = EASIER_WORD_DEFINITION
        sentence_rule = SENTENCE_SHORTENING

    sections = [
        REWRITE_ROLE,
        f"\n{word_rule}",
        f"\n{sentence_rule}",
        f"\n{freedom}",
        f"\n{SCORER_LEVERS}",
        f"\n{AIM_FOR_BAND}",
        f"\n{_effort_plan(diagnostics)}",
        f"\n{REWRITE_INVARIANTS}",
        f"\n{REWRITE_OUTPUT_FORMAT}",
        f"\nTarget reading level: {band.display} "
        f"(estimated grade {band.low:.1f} to {band.high:.1f}). "
        f"Aim near the middle, about grade {band.center:.1f}, not the edges.",
        direction,
    ]

    if diagnostics.protected_terms:
        terms = ", ".join(f'"{term}"' for term in diagnostics.protected_terms)
        sections.append(
            f"\nProtected terms — these MUST appear in your rewrite exactly as "
            f"written, however difficult they are: {terms}"
        )

    if diagnostics.hard_words and not raising:
        sections.append(
            _hard_words_heading(diagnostics) + _describe_hard_words(diagnostics)
        )

    if diagnostics.long_sentences and not raising:
        sections.append(
            f"\nLong sentences — each of these must come in under "
            f"{diagnostics.sentence_limit} words. Split them; do not delete "
            f"what they say:\n" + _describe_long_sentences(diagnostics)
        )
    elif _syntax_deficit(diagnostics) >= _SYNTAX_DEFICIT_WORDS:
        sections.append(
            f"\nNo sentence is over the {diagnostics.sentence_limit}-word "
            f"ceiling, but the average is still too high. Split several "
            f"mid-length sentences so the average falls to about "
            f"{_target_mean_sentence_length(diagnostics):.1f} words."
        )

    if feedback:
        sections.append(f"\nYour previous attempt was rejected because {feedback}")

    sections.append(f"\nPassage to rewrite:\n{passage}")
    return "\n".join(sections)


def parse_rewrite_output(raw_text: str) -> str:
    """Pull the passage out of the model's response.

    Falls back to the raw text so a model that ignores the JSON instruction
    still gets its output verified rather than discarded -- the gates decide,
    not the formatting.
    """
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("passage"):
            # Drop any grade/level fields the model added. score_text is the
            # only source of the reported grade.
            return str(parsed["passage"]).strip()
    except json.JSONDecodeError:
        pass

    match = re.search(r'"passage"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.DOTALL)
    if match:
        return json.loads(f'"{match.group(1)}"').strip()

    return text


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _rejection_feedback(score, band) -> str:
    midpoint = band.center
    current_mean = score.features.mean_sentence_length
    needed = _mean_length_for_grade(midpoint, score.features.mean_log_word_freq)
    ceiling = float(rl_bands.max_sentence_length(band))
    needed = min(max(needed, _MIN_TARGET_MEAN_LENGTH), ceiling)

    if score.estimated_grade > band.high:
        if current_mean > needed + 0.5:
            lever = (
                f"It is still too hard. Sentences average {current_mean:.1f} "
                f"words; they need to average about {needed:.1f}. Split more "
                f"sentences, including ones that were not on the original list. "
                f"Do not only swap a few nouns and leave 16-word sentences."
            )
        else:
            lever = (
                "It is still too hard. Replace a few more of the rarest "
                "content words. Do not flatten the rest of the passage."
            )
    else:
        lever = (
            "It is too easy. Put back some "
            "mid-level content words and slightly longer sentences so it "
            "reads near the middle of the band, not like a much younger grade."
        )
    return (
        f"it still measured at grade {score.estimated_grade:.1f}, outside "
        f"{band.low:.1f}-{band.high:.1f} (aim near {midpoint:.1f}). {lever}"
    )


def _distance_to_band(grade: float, band) -> float:
    if grade > band.high:
        return grade - band.high
    if grade < band.low:
        return band.low - grade
    return 0.0


def _closest_attempt(attempts: list[RewriteAttempt], band) -> RewriteAttempt:
    return min(attempts, key=lambda item: _distance_to_band(item.estimated_grade, band))


def escalate_to_rewrite(
    correction: CorrectionResult,
    target_grade,
    *,
    generate,
    protected_terms=None,
    table=None,
    max_attempts=None,
    passage_type=None,
    keep_closest=False,
) -> RewriteOutcome:
    """Run Operator D on a correction that failed its gate.

    `generate` is injected so this is testable without a network call.

    `keep_closest` is for the teacher-facing corrector: if nothing lands in
    band, still surface the draft nearest the band so a human can read it.
    The student-facing generation path leaves this off and keeps the
    deterministic fallback, then raises.
    """
    band = correction.target_band
    protected = list(protected_terms or [])
    budget = max_attempts or rl_config.MAX_GENERATION_ATTEMPTS

    outcome = RewriteOutcome(
        text=correction.text,
        correction=correction,
        final_score=correction.final_score,
        gate_passed=False,
        failure_reason=correction.failure_reason,
    )

    feedback = None
    verify_drift = rl_config.correction_mode() == rl_config.CORRECTION_MODE_SINGLE_PASS_LLM
    for attempt in range(1, budget + 1):
        diagnostics = build_diagnostics(
            correction.text,
            target_grade,
            protected_terms=protected,
            prior_result=correction,
            table=table,
            passage_type=passage_type,
        )
        prompt = build_rewrite_prompt(correction.text, diagnostics, feedback)

        try:
            candidate = parse_rewrite_output(generate(prompt))
        except Exception as exc:
            _log("rewrite_call_failed", attempt=attempt, error=str(exc))
            outcome.llm_attempts = attempt
            outcome.failure_reason = "rewrite_call_failed"
            return outcome

        outcome.llm_attempts = attempt

        if not candidate.strip():
            feedback = "it returned an empty passage."
            continue

        score = score_text(candidate, table=table)
        in_band = rl_bands.in_band(score, band)
        drift = None
        if verify_drift:
            drift = check_semantic_drift(
                correction.original_text,
                candidate,
                protected_terms=protected,
                passage_type=diagnostics.passage_type,
            )
            outcome.drift_report = drift
            _log(
                "rewrite_drift_check",
                attempt=attempt,
                passed=drift.passed,
                reasons=drift.reasons,
            )
            if not drift.passed:
                feedback = (
                    "the rewrite drifted from the original: "
                    + "; ".join(drift.reasons)
                )
                record = RewriteAttempt(
                    attempt=attempt,
                    text=candidate,
                    estimated_grade=score.estimated_grade,
                    in_band=False,
                )
                outcome.rewrite_attempts.append(record)
                continue

        _log(
            "rewrite_attempt",
            attempt=attempt,
            band=band.label,
            passage_type=diagnostics.passage_type,
            passage_type_specified=diagnostics.passage_type_specified,
            estimated_grade=round(score.estimated_grade, 2),
            in_band=in_band,
        )

        record = RewriteAttempt(
            attempt=attempt,
            text=candidate,
            estimated_grade=score.estimated_grade,
            in_band=in_band,
        )
        outcome.rewrite_attempts.append(record)

        if in_band:
            outcome.text = candidate
            outcome.final_score = score
            outcome.gate_passed = True
            outcome.failure_reason = None
            outcome.llm_rewrite_applied = True
            return outcome

        feedback = _rejection_feedback(score, band)

    outcome.failure_reason = "rewrite_rejected_out_of_band"
    if verify_drift and outcome.drift_report is not None and not outcome.drift_report.passed:
        outcome.failure_reason = "rewrite_rejected_semantic_drift"
        # Keep the original uncorrected passage. A drifted rewrite is worse
        # than an honest "still above band" on the source.
        outcome.text = correction.original_text
        outcome.final_score = correction.initial_score
        outcome.llm_rewrite_applied = False
        outcome.gate_passed = correction.gate_passed
    elif keep_closest and outcome.rewrite_attempts:
        best = _closest_attempt(outcome.rewrite_attempts, band)
        outcome.text = best.text
        outcome.final_score = score_text(best.text, table=table)
        outcome.llm_rewrite_applied = True
    _log(
        "rewrite_exhausted",
        attempts=outcome.llm_attempts,
        band=band.label,
        failure_reason=outcome.failure_reason,
        kept_closest=keep_closest and bool(outcome.rewrite_attempts),
    )
    return outcome


# --------------------------------------------------------------------------
# Part 4: naturalness. Sampled, non-blocking, off by default.
# --------------------------------------------------------------------------


NATURALNESS_PROMPT = """You are reviewing a reading passage written for a specific grade level.

Rate how naturally it reads for that grade: does it sound like prose written for these students, or like an adult passage with words mechanically swapped out?

Respond with ONLY valid JSON in this exact shape:
{"score": 1-5, "rationale": "one sentence"}"""


def _should_sample(rng) -> bool:
    return rng.random() < rl_config.NATURALNESS_SAMPLE_RATE


def check_naturalness(passage, band, *, generate, rng=None) -> dict | None:
    """Log a qualitative read on the passage. Never gates, never raises.

    This is the one judgement an LLM adds that the quantitative scorer cannot:
    the scorer can confirm a passage is at grade 4, not that it reads like it
    was written for a nine-year-old rather than deflated from an adult draft.

    It stays advisory because it is the least reproducible check in the system
    and costs another call, while a false negative here is harmless -- unlike
    an off-band passage, which is why that still gates and this does not.
    Output is logged in a parseable shape so the reviewed samples accumulate
    into fine-tuning data.
    """
    if not rl_config.ENABLE_NATURALNESS_CHECK:
        return None
    if not _should_sample(rng or random):
        return None

    prompt = (
        f"{NATURALNESS_PROMPT}\n\nTarget level: {band.display}\n\n"
        f"Passage:\n{passage}"
    )

    try:
        parsed = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", generate(prompt).strip()))
        report = {
            "score": parsed.get("score"),
            "rationale": parsed.get("rationale"),
        }
    except Exception as exc:
        _log("naturalness_check_failed", band=band.label, error=str(exc))
        return None

    _log("naturalness_checked", band=band.label, passage=passage, **report)
    return report
