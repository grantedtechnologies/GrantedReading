"""Tunable constants for the reading_level package.

Every threshold, coefficient, and limit that affects measurement or repair
lives here. No other module in this package should contain a bare numeric
literal that changes behaviour.

Values marked UNCALIBRATED are placeholders chosen for plausible ordering,
not fitted against labeled data. See docs/calibration.md.
"""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
FREQUENCY_TABLE_PATH = DATA_DIR / "frequency_table.json.gz"
FIXTURES_DIR = DATA_DIR / "fixtures"

# Parser model, used for tokenising, POS tagging, lemmatising, and dependency
# parsing. Its word vectors are NOT used anywhere in this package: the pruned
# table (20k rows serving ~685k keys) collapses distinct words onto shared rows,
# so "melancholic", "gloomy", "wistful", and "pensive" all scored cosine 1.0
# against each other while "sad" scored 0.47. Meaning guarding is done in
# WordNet instead (see MIN_SENSE_SIMILARITY).
#
# Because vectors are now unused, a smaller model (en_core_web_sm) would
# probably serve identically and save ~40MB. Not swapped yet -- unverified
# against parse quality for the split operator's fragment checks.
SPACY_MODEL = "en_core_web_md"

# --------------------------------------------------------------------------
# Frequency table
# --------------------------------------------------------------------------

# Returned for words absent from the table. Set to the 5th percentile of the
# shipped table (measured p05 = 0.1055) rather than 0.0: zero means "maximally
# rare", which is the wrong assumption for typos, names, and coinages.
OOV_LOGFREQ = 0.11

# During table construction an inflected form inherits its lemma's frequency
# when the lemma is at least this much more common (log10 units). A reader who
# knows "run" can handle "running".
LEMMA_INHERITANCE_LOGFREQ_GAP = 0.30

# Build-time: share of corpus occurrences that must be capitalised before a
# word is flagged as a proper noun.
PROPER_NOUN_MIN_CAPITAL_RATIO = 0.70

# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

# Below this word count, readability statistics are not meaningful and the
# score is reported with confidence="low". Question stems will always hit this.
MIN_RELIABLE_WORDS = 50

# A content word at or below this log10 frequency (~15 occurrences per million)
# counts toward hard_word_ratio and becomes a substitution target. Measured
# against the shipped table this yields hard-word ratios of roughly 0.03 for a
# primary passage, 0.14 for upper elementary, and 0.48 for secondary prose.
HARD_WORD_LOGFREQ_THRESHOLD = 1.20

# Words at or above this frequency percentile count as "familiar" for the
# Dale-Chall-style cross-check. The published Dale-Chall 3000-word list is not
# bundled; the top ~5% of a 57k-entry table is used as a stand-in of comparable
# size. This makes the metric Dale-Chall-shaped, not Dale-Chall.
# See docs/calibration.md.
FAMILIAR_WORD_PERCENTILE = 95.0

# UNCALIBRATED -- placeholder pending regression fit against a labeled corpus.
# See docs/calibration.md. Do not treat output as accurate until fitted.
# Eyeballed against the three fixture passages in data/fixtures so that a
# primary passage lands near grade 1.5, upper elementary near 6, and dense
# secondary prose at the ceiling. Three hand-written passages are not a
# calibration set: trust the ordering, not the numbers.
GRADE_COEF_WORD_FREQ = -2.60
GRADE_COEF_SENTENCE_LENGTH = 0.50
GRADE_INTERCEPT = 4.46

# Reported grades are clamped to the band range the system supports.
GRADE_FLOOR = 0.0
GRADE_CEILING = 13.0

# Flesch-Kincaid grade level coefficients (published formula, not fitted here).
FK_COEF_SENTENCE_LENGTH = 0.39
FK_COEF_SYLLABLES = 11.8
FK_INTERCEPT = -15.59

# Dale-Chall raw-score coefficients (published formula). The +3.6365 penalty
# applies when more than DALE_CHALL_PENALTY_THRESHOLD percent of words are
# unfamiliar.
DALE_CHALL_COEF_DIFFICULT = 0.1579
DALE_CHALL_COEF_SENTENCE_LENGTH = 0.0496
DALE_CHALL_PENALTY = 3.6365
DALE_CHALL_PENALTY_THRESHOLD = 5.0

# Maps a Dale-Chall raw score to an approximate grade level (upper bound of raw
# score -> representative grade), from the published cutoff table.
DALE_CHALL_GRADE_TABLE = (
    (4.9, 3.0),
    (5.9, 5.5),
    (6.9, 7.5),
    (7.9, 9.5),
    (8.9, 11.5),
    (9.9, 13.0),
)
DALE_CHALL_MAX_GRADE = 15.0

# --------------------------------------------------------------------------
# Grade bands
# --------------------------------------------------------------------------

# (label, display, numeric grade). Bands are data, never conditionals.
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

# Bands are ranges, never points: grade N spans [N - 0.5, N + 0.5].
BAND_HALF_WIDTH = 0.5

# Sentence-length ceiling for a band: intercept + slope * grade.
# UNCALIBRATED -- rough guidance from basal-reader norms, not fitted.
MAX_SENTENCE_LENGTH_INTERCEPT = 8.0
MAX_SENTENCE_LENGTH_SLOPE = 1.0

# Question stems are too short to measure reliably, so their band is widened by
# this many grade levels before reporting. They are logged, never hard-gated.
QUESTION_BAND_TOLERANCE = 1.5

# --------------------------------------------------------------------------
# Repair
# --------------------------------------------------------------------------

# Minimum number of control-loop passes, and the value used when a caller does
# not ask for a specific budget on a passage that is already near band.
MAX_ITERATIONS = 5

# Extra control-loop passes granted per grade level of gap between the initial
# score and the near edge of the target band. Splitting moves the score in
# large steps (it divides by sentence count) but substitution moves it in small
# ones (it divides by word count): a single substitution on a 90-content-word
# passage shifts the estimate by roughly 0.03-0.05 grade levels, so a two-grade
# gap closed by vocabulary alone needs tens of edits, not five.
#
# UNCALIBRATED -- placeholder, needs real tuning.
BASE_ITERATIONS_PER_GRADE_GAP = 4

# Absolute cap regardless of gap size. This runs in a user-facing request path
# and must not be allowed to run unbounded on a pathological input.
ITERATION_HARD_CEILING = 40

# Substitutions applied per control-loop pass before the passage is re-scored.
# Each one still records its own edit; only the full-passage re-score is shared,
# which is where the cost sits.
#
# UNCALIBRATED -- placeholder, needs real tuning. Raising it trades precision
# for speed: the batch commits to several edits against one stale score.
SUBSTITUTION_BATCH_SIZE = 4

# Meaning guard for candidates drawn from a sense other than the original
# word's dominant one. Candidates from the dominant synset are synonyms by
# construction and skip this check; other senses are compared to the dominant
# sense with WordNet's Wu-Palmer similarity, which is a taxonomy distance in
# [0, 1] and is NOT on the same scale as the cosine similarity this replaced.
#
# Now that MAX_SYNSETS_PER_WORD is 1, the only way a non-dominant sense reaches
# this check is when word sense disambiguation picked one, so this is the safety
# net on a Lesk result that drifts far from the dominant reading.
#
# UNCALIBRATED -- placeholder pending tuning against human judgements of
# whether a substitution preserved meaning. Measured against the corpus:
# adjective satellite senses of the same word plateau at exactly 0.50
# (arduous, resilient), while genuinely distant noun senses fall well below it
# (cacophony sense 2 = 0.27). A cut of 0.50 therefore admits sibling adjective
# senses and rejects distant noun senses -- but it has almost no discriminative
# power for adjectives, which all sit on the plateau.
MIN_SENSE_SIMILARITY = 0.50

# A replacement must be at least this much more frequent (log10 units) than the
# word it replaces, otherwise the edit buys nothing.
#
# UNCALIBRATED -- needs tuning against a real corpus. Lowered from 0.40, which
# required a ~2.5x frequency multiple and rejected close synonyms that sit at
# similar frequency levels (melancholic -> melancholy is only 0.34). Note that
# a floor at or below 0.0 would let the operator swap in a RARER word, which
# defeats its purpose, so this stays positive.
MIN_FREQ_GAIN = 0.15

# Synsets considered when word sense disambiguation cannot resolve the usage.
# WordNet orders synsets by sense frequency, so index 0 is the dominant sense.
#
# Lowered from 3 to 1 on measured evidence: every bad substitution observed on
# the business-register passage came from a NON-dominant sense of the original
# word ("careful" is sense 1 of "deliberate", "lucky" reaches "favorable"
# through sense 2), while every substitution judged good came from sense 0
# (arduous -> hard, cacophony -> din, tenacity -> persistence,
# instinctive -> natural, curriculum -> program, ambition -> dream). Admitting
# secondary senses cost meaning and bought nothing.
MAX_SYNSETS_PER_WORD = 1

# Candidate replacements considered per word before giving up.
MAX_SUBSTITUTION_CANDIDATES = 12

# Tokens on each side of the target used for the substitution similarity
# check. A full-sentence compare lets one swapped word hide behind twenty
# unchanged ones: postpone→table is 0.933 in a 23-word sentence (above the
# floor) and 0.851 in a ±3 window (below it). Radius 3 is the smallest
# window that still keeps detrimental→damaging at 0.949; radius 2 drops
# that pair to 0.734.
#
# UNCALIBRATED.
SUBSTITUTION_CONTEXT_RADIUS = 3

# Floor for that window comparison, using all-mpnet-base-v2. WordNet stays
# the only candidate source; this only vetoes lemmas that share a synset
# but are not substitutable in the local context.
#
# UNCALIBRATED. Measured on the 8th-grade committee/motive sentences:
#   reject  postpone→table 0.851, motive→need 0.745
#   keep    postpone→delay 0.962, motive→motivation 0.966,
#           detrimental→damaging 0.949
# 0.92 sits in that gap. It still cannot see ensuing→resulting (0.960 in
# "the ensuing weeks") -- those are near-paraphrases to the encoder.
MIN_SUBSTITUTION_SENTENCE_SIMILARITY = 0.92

# Coordinating conjunctions that may become a sentence boundary.
SPLITTABLE_COORDINATORS = ("and", "but", "so", "or")

# Subordinators that can be re-expressed as a sentence-initial connective while
# keeping the causal/contrastive relationship. Anything not listed is skipped
# rather than guessed at.
SUBORDINATOR_REPLACEMENTS = {
    "because": "This is because",
    "since": "This is because",
    "although": "But",
    "though": "But",
    "while": "At the same time,",
    "whereas": "But",
    "when": "Then",
}
ENABLE_SUBORDINATE_SPLIT = True

# Operator C carries the highest meaning-distortion risk, so it is off by
# default and only runs when splitting and substitution leave a gap.
ENABLE_CLAUSE_SIMPLIFICATION = False

# --------------------------------------------------------------------------
# Generation gate (used by the caller, not by this package)
# --------------------------------------------------------------------------

# Total LLM calls allowed per user request. This is a user-facing request path
# and must not loop unbounded.
MAX_GENERATION_ATTEMPTS = 2

# --------------------------------------------------------------------------
# Correction mode
#
# The iterative loop (Operators A–C in repair.py) is the Railway CPU cost:
# up to MAX_ITERATIONS / ITERATION_HARD_CEILING passes, each one re-running
# spaCy, WordNet + Lesk, and score_text. The LLM rewrite (Operator D) is
# network-bound on Azure, not local CPU. There is also no Lexile-calibrated
# training data yet to justify that extra iterative precision on first
# deploy. This flag lets a deployment skip A–C and go diagnostics → one
# Operator D call, without deleting the iterative path.
#
# Values (set CORRECTION_MODE in the environment):
#   iterative       — today's default: A–C loop, then Operator D if it
#                     plateaus. Fully working; leave this on when you want
#                     the original precision back.
#   single_pass_llm — skip A–C. Score and diagnose once, then one Operator D
#                     rewrite. score_text still reports the grade; the LLM
#                     never does. check_semantic_drift still runs.
# --------------------------------------------------------------------------
CORRECTION_MODE_ITERATIVE = "iterative"
CORRECTION_MODE_SINGLE_PASS_LLM = "single_pass_llm"
CORRECTION_MODES = (
    CORRECTION_MODE_ITERATIVE,
    CORRECTION_MODE_SINGLE_PASS_LLM,
)
DEFAULT_CORRECTION_MODE = CORRECTION_MODE_ITERATIVE

# --------------------------------------------------------------------------
# Operator D: diagnostics and rewrite verification
#
# The rewrite itself is an LLM call and lives in ai.py. This package only
# builds the diagnostic package handed to it and verifies what comes back;
# nothing here may import an LLM client.
# --------------------------------------------------------------------------

# Sentence embedding model for meaning-drift detection.
#
# Chosen by measurement against tests/drift_cases.py, not by reputation. spaCy
# vectors were evaluated first and rejected: averaged over a passage they
# compress everything toward the corpus mean, so an unrelated passage scored
# 0.900 (en_core_web_md) and 0.865 (en_core_web_lg) against the business
# fixture, versus 0.934 and 0.868 for a faithful rewrite of it. Both give
# NEGATIVE separation -- no threshold exists. A static-embedding alternative
# (model2vec) was also rejected at -0.114. all-mpnet-base-v2 separates the same
# set by +0.346 at sentence level.
DRIFT_EMBEDDING_MODEL = "all-mpnet-base-v2"

# Sentence-level floor: the least similar sentence in the rewrite, matched
# against its best counterpart in the original. This is the real gate. A
# passage-level average hides one badly drifted sentence, which is the failure
# mode that matters.
#
# UNCALIBRATED -- placeholder. Measured against tests/drift_cases.py, faithful
# rewrites floor at 0.584 and drifted ones peak at 0.239, so 0.40 sits mid-gap.
# Six hand-written cases are not a calibration set.
MIN_SENTENCE_SIMILARITY = 0.40

# Passage-level floor. Advisory rather than decisive: on the same set the
# passage-level margin was only +0.009 (0.704 faithful vs 0.695 drifted), so
# this is set well below the observed faithful minimum and catches only gross
# whole-passage replacement.
#
# UNCALIBRATED -- placeholder.
MAX_PASSAGE_DRIFT = 0.50

# Extra negation markers the rewrite may introduce relative to the original.
#
# Embeddings are blind to polarity: a rewrite asserting the opposite of the
# source scored 0.695 passage-level, above a faithful rewrite at 0.704. Counting
# negation directly separated that case cleanly (+3 markers against +0 for every
# faithful rewrite), so it is a hard gate rather than a similarity input.
#
# UNCALIBRATED -- placeholder. Zero tolerance is deliberately strict; a rewrite
# that legitimately needs a new "not" can be rejected and retried.
MAX_NEGATION_DELTA = 0

# Words that flip polarity without being tagged as negation by the parser.
NEGATION_MARKERS = frozenset(
    {
        "no", "not", "never", "rarely", "seldom", "without", "nor", "none",
        "neither", "hardly", "barely", "cannot", "n't", "nothing", "nobody",
    }
)

# Hard words and over-length sentences handed to the rewrite prompt. Capped so
# the prompt stays readable; the worst offenders come first either way.
MAX_DIAGNOSTIC_HARD_WORDS = 20
MAX_DIAGNOSTIC_SUGGESTIONS = 3

# How much structural freedom the rewrite prompt grants.
#
# A narrative may be restructured -- plot, ending, order of events -- because
# nobody downstream is checking those details against an original the teacher
# never sees. An informational passage may not: comprehension questions are
# built on its facts, so wording is free and content is not.
PASSAGE_TYPE_NARRATIVE = "narrative"
PASSAGE_TYPE_INFORMATIONAL = "informational"
PASSAGE_TYPES = (PASSAGE_TYPE_NARRATIVE, PASSAGE_TYPE_INFORMATIONAL)

# Used when a caller does not say which it is. Deliberately the stricter tier:
# granting narrative freedom to an unlabelled passage would let a rewrite
# restructure away a fact a question depends on, and the drift gates cannot see
# that happen. Falling back here also logs a warning -- the fallback exists to
# keep an unlabelled call safe, not to make labelling optional.
FALLBACK_PASSAGE_TYPE = PASSAGE_TYPE_INFORMATIONAL

# Fact checks run for these passage types only. A narrative may drop a detail
# -- that is precisely the freedom the narrative tier grants -- while an
# informational passage may not, because its numbers are what the comprehension
# questions ask about.
FACT_CHECKED_PASSAGE_TYPES = frozenset({PASSAGE_TYPE_INFORMATIONAL})

# Entity labels treated as facts a rewrite may not drop.
#
# Deliberately excludes DATE and TIME, which spaCy applies to vague temporal
# phrases: measured on these fixtures it tags "quarterly", "the morning", and
# "the sweltering afternoons", and a legitimate grade-6 rewrite turns
# "quarterly" into "every three months". Gating on those would reject good
# rewrites, which the negation threshold has already shown to be expensive.
# Numeric labels (CARDINAL, QUANTITY, PERCENT, MONEY) are excluded because the
# number check covers their content and compares by value, not surface form.
FACT_ENTITY_LABELS = frozenset(
    {
        "PERSON", "ORG", "GPE", "LOC", "NORP", "FAC", "EVENT",
        "WORK_OF_ART", "LAW", "PRODUCT", "LANGUAGE",
    }
)

# Number words spelled out. spaCy flags these with `like_num` but leaves them
# as text, so "three" and "3" would otherwise count as two different facts.
NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "thousand": 1000, "million": 1000000, "billion": 1000000000,
}

# Qualitative "does this read like it was written for this grade" check.
#
# Off by default and non-blocking by design: it is an LLM judgement, so it is
# less reproducible than the deterministic gates, it costs another call, and a
# false negative is not dangerous the way an off-band or drifted passage is.
# When enabled it logs for later review and never rejects output.
ENABLE_NATURALNESS_CHECK = False

# Share of eligible passages sampled when the check is enabled.
NATURALNESS_SAMPLE_RATE = 0.05

# --------------------------------------------------------------------------
# Worksheet modulator (passage + questions)
# --------------------------------------------------------------------------

# Ceiling on source-material size. Over this, reject — do not truncate and
# do not retrieve. Teachers should paste the chapter this worksheet covers.
# UNCALIBRATED -- 45,000 is a first-pass cap, not fitted to real packets.
SOURCE_MATERIAL_MAX_TOKENS = 45000

# UNCALIBRATED -- chars-per-token stand-in. This is not a model tokenizer.
SOURCE_CHARS_PER_TOKEN = 4

# How far the achieved DOK mix may drift from the target mix, as a share of
# the question count (0.25 = one question in four). UNCALIBRATED.
DOK_MIX_TOLERANCE = 0.25

# Default DOK mix by numeric grade, percents summing to 100.
# UNCALIBRATED -- placeholder pending real assessment blueprints.
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


def correction_mode() -> str:
    """Which correction path a request takes. Unknown values fall back to iterative."""
    raw = (os.getenv("CORRECTION_MODE") or DEFAULT_CORRECTION_MODE).strip().lower()
    if raw in CORRECTION_MODES:
        return raw
    return DEFAULT_CORRECTION_MODE
