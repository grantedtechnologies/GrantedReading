"""Repair operators and the correction control loop.

Repair is targeted surgery on failing sentences, never regeneration:
regenerating would discard the topical content the passage was built around.

Nothing in this module calls a language model. Every operator is deterministic
so the whole pipeline is unit-testable offline.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import NamedTuple

from . import bands, config
from ._nlp import get_lesk, get_nlp, get_sentence_encoder, get_wordnet
from .frequency import FrequencyTable, get_default_table
from .scorer import ReadingLevelScore, parse_sentences, score_text

OPERATOR_SPLIT = "split_sentence"
OPERATOR_SUBSTITUTE = "substitute_word"
OPERATOR_SIMPLIFY = "simplify_clause"

REASON_EMPTY = "empty_text"
REASON_ABOVE_BAND = "above_target_band"
REASON_BELOW_BAND = "below_target_band"
REASON_MAX_ITERATIONS = "max_iterations_reached"
REASON_NO_EDITS = "no_applicable_edit"

_SUBJECT_DEPS = frozenset({"nsubj", "nsubjpass", "expl", "csubj", "csubjpass"})
_FINITE_TAGS = frozenset({"VBZ", "VBD", "VBP", "MD"})

# spaCy coarse POS -> WordNet POS
_WORDNET_POS = {"NOUN": "n", "VERB": "v", "ADJ": "a", "ADV": "r"}

# Tags whose surface form we can reproduce from a lemma with confidence.
_INFLECTABLE_TAGS = frozenset(
    {"NN", "NNS", "VB", "VBP", "VBZ", "VBD", "VBN", "VBG", "JJ", "RB"}
)

_PARENTHETICAL = re.compile(r"\s*\([^()]*\)")


@dataclass
class RepairEdit:
    operator: str
    sentence_index: int
    before: str
    after: str
    rationale: str
    # Why the control loop picked this operator over the alternatives on this
    # iteration. Populated by correct_text, not by the operators themselves.
    selection: str = ""
    # True when the sense was resolved from context, False when the operator
    # fell back to WordNet's dominant sense. Substitutions on the fallback path
    # are the less reliable ones, and this says which is which without having to
    # reconstruct the run.
    sense_disambiguated: bool = False


@dataclass
class CorrectionResult:
    text: str
    original_text: str
    final_score: ReadingLevelScore
    initial_score: ReadingLevelScore
    target_band: bands.Band
    in_band: bool
    iterations: int
    edits: list[RepairEdit] = field(default_factory=list)
    protected_terms_preserved: list[str] = field(default_factory=list)
    gate_passed: bool = False
    failure_reason: str | None = None
    # Estimated grade after each iteration, so a run can be read as converging,
    # plateauing, or oscillating without instrumenting the loop by hand.
    score_trajectory: list[float] = field(default_factory=list)


# --------------------------------------------------------------------------
# Grammatical helpers
# --------------------------------------------------------------------------


def _is_finite(token) -> bool:
    return token.tag_ in _FINITE_TAGS or "Fin" in token.morph.get("VerbForm")


def _is_independent_clause(text: str) -> bool:
    """True when `text` stands alone: its ROOT has a subject and a finite verb.

    A fragment is worse than a long sentence, so every split is checked. The
    subject must belong to the ROOT specifically -- an embedded subject is not
    enough, or "record how tall the plants grow" would pass on the strength of
    "plants".
    """
    stripped = text.strip()
    if not stripped:
        return False

    doc = get_nlp()(stripped)
    roots = [token for token in doc if token.dep_ == "ROOT"]
    if not roots:
        return False

    root = roots[0]
    has_subject = any(child.dep_ in _SUBJECT_DEPS for child in root.children)

    # Passives and periphrastic tenses carry finiteness on the auxiliary.
    finite_candidates = [root] + [
        child for child in root.children if child.dep_ in {"aux", "auxpass", "cop"}
    ]
    return has_subject and any(_is_finite(token) for token in finite_candidates)


def _tidy(fragment: str) -> str:
    """Normalise a clause into a standalone sentence."""
    text = fragment.strip().strip(",;:").strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def _pluralise(word: str) -> str:
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


def _past_tense(word: str) -> str:
    if word.endswith("e"):
        return word + "d"
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ied"
    return word + "ed"


def _present_participle(word: str) -> str:
    if word.endswith("e") and not word.endswith("ee"):
        return word[:-1] + "ing"
    return word + "ing"


def _inflect(lemma: str, tag: str) -> str | None:
    """Render `lemma` in the surface form implied by `tag`.

    Returns None when the inflection cannot be produced by regular rules,
    so irregular forms are skipped rather than mangled.
    """
    if tag in {"NN", "VB", "VBP", "JJ", "RB"}:
        return lemma
    if tag == "NNS":
        return _pluralise(lemma)
    if tag == "VBZ":
        return _pluralise(lemma)
    if tag in {"VBD", "VBN"}:
        return _past_tense(lemma)
    if tag == "VBG":
        return _present_participle(lemma)
    return None


def _match_case(replacement: str, original: str) -> str:
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _realised_replacement(lemma: str, token, table: FrequencyTable) -> str | None:
    """The surface form that would actually be inserted for `lemma`.

    None when the inflection cannot be produced, or when regular rules invent
    an unattested form ("dealed"). The sentence-similarity filter and the
    apply path must agree on this, or the filter would judge a sentence the
    student never reads.
    """
    inflected = _inflect(lemma, token.tag_)
    if inflected is None:
        return None
    if inflected != lemma and not table.contains(inflected):
        return None
    return _match_case(inflected, token.text)


def _swap_in_sentence(token, replacement: str) -> str:
    """Replace only this occurrence of the token, leaving the rest intact."""
    sentence = token.doc.text
    return sentence[: token.idx] + replacement + sentence[token.idx + len(token.text) :]


def _context_window(token, replacement: str | None = None) -> str:
    """The ±N-token span around `token`, optionally with `token` swapped out.

    Full-sentence comparison is too coarse: twenty unchanged words pull the
    embedding toward 1.0 no matter what landed in the one edited slot. The
    window is sliced from the original document text so whitespace matches
    what `_swap_in_sentence` would produce.
    """
    doc = token.doc
    radius = config.SUBSTITUTION_CONTEXT_RADIUS
    start_i = max(0, token.i - radius)
    end_i = min(len(doc), token.i + radius + 1)
    start_char = doc[start_i].idx
    end_char = doc[end_i - 1].idx + len(doc[end_i - 1])
    if replacement is None:
        return doc.text[start_char:end_char]
    return (
        doc.text[start_char : token.idx]
        + replacement
        + doc.text[token.idx + len(token.text) : end_char]
    )


def _sense_similarity(wordnet, primary, synset) -> float | None:
    """How close a sense is to the word's dominant sense, in WordNet's taxonomy.

    This replaces cosine similarity over spaCy vectors, which was measuring the
    vector table rather than meaning: en_core_web_md prunes ~685k keys down to
    20k rows, so "melancholic", "gloomy", "wistful", and "pensive" all scored
    exactly 1.0 against each other while "sad" -- a genuine synonym with a large
    frequency gain -- scored 0.47 and was vetoed.

    Returns None when the two senses are not comparable in the taxonomy, which
    is treated as a rejection.
    """
    if synset == primary:
        # Same synset: synonymy holds by construction, no distance needed.
        return 1.0
    return wordnet.wup_similarity(primary, synset)


# --------------------------------------------------------------------------
# Operator A: sentence splitting (lowers syntactic difficulty)
# --------------------------------------------------------------------------


def _split_candidates(doc):
    """Yield (kind, token) split points, coordination first."""
    coordinators = []
    subordinators = []

    for token in doc:
        if token.i == 0:
            continue
        if token.dep_ == "cc" and token.lower_ in config.SPLITTABLE_COORDINATORS:
            coordinators.append(token)
        elif (
            config.ENABLE_SUBORDINATE_SPLIT
            and token.dep_ == "mark"
            and token.lower_ in config.SUBORDINATOR_REPLACEMENTS
        ):
            subordinators.append(token)

    for token in coordinators:
        yield "coordination", token
    for token in subordinators:
        yield "subordination", token


def _try_split(doc) -> tuple[str, str] | None:
    """Split one sentence into two independent clauses, or return None."""
    text = doc.text

    for kind, token in _split_candidates(doc):
        left_raw = text[: token.idx]
        right_raw = text[token.idx + len(token.text) :]

        if kind == "coordination":
            left, right = _tidy(left_raw), _tidy(right_raw)
            if not left or not right:
                continue
            if _is_independent_clause(left) and _is_independent_clause(right):
                return left, right
            continue

        # An adverb modifying the marker ("largely because") would be stranded
        # on the left clause. Skip and let another operator take the sentence.
        if token.i > 0 and doc[token.i - 1].pos_ == "ADV":
            continue

        # Subordinate clauses lose their subject when the marker is dropped, so
        # the relationship is restored with an explicit connective.
        connective = config.SUBORDINATOR_REPLACEMENTS[token.lower_]
        left = _tidy(left_raw)
        clause = _tidy(right_raw)
        if not left or not clause:
            continue
        if not (_is_independent_clause(left) and _is_independent_clause(clause)):
            continue

        joined = f"{connective} {clause[0].lower() + clause[1:]}"
        return left, _tidy(joined)

    return None


def _apply_split(sentences, band):
    """Split the longest over-length sentence. Returns a RepairEdit or None."""
    limit = bands.max_sentence_length(band)
    docs = [get_nlp()(sentence) for sentence in sentences]

    lengths = [
        (sum(1 for token in doc if token.is_alpha), index)
        for index, doc in enumerate(docs)
    ]
    lengths.sort(reverse=True)

    for length, index in lengths:
        if length <= limit:
            break
        split = _try_split(docs[index])
        if split is None:
            continue
        left, right = split
        before = sentences[index]
        sentences[index : index + 1] = [left, right]
        return RepairEdit(
            operator=OPERATOR_SPLIT,
            sentence_index=index,
            before=before,
            after=f"{left} {right}",
            rationale=(
                f"Sentence ran {length} words against a {limit}-word ceiling "
                f"for {band.display}; split into two independent clauses."
            ),
        )
    return None


# --------------------------------------------------------------------------
# Operator B: constrained synonym substitution (lowers semantic difficulty)
# --------------------------------------------------------------------------


class Candidate(NamedTuple):
    word: str
    gain: float
    sense_similarity: float
    disambiguated: bool
    sense_label: str


def _sense_label(synset) -> str:
    """A short human-readable name for a sense, for the edit rationale.

    Prefers a sibling lemma ("intentional" for the deliberate/intentional
    sense) because it reads better than a gloss; falls back to a truncated
    definition when the synset has only the one lemma.
    """
    definition = synset.definition() or synset.name()
    if len(definition) > 48:
        definition = definition[:45].rstrip() + "..."
    return definition


def _disambiguate_sense(token, lemma: str, wordnet_pos: str):
    """Resolve which sense of `token` its sentence is using, or None.

    Uses the sentence the token was parsed from as the Lesk context window, so
    no signature change is needed to reach it.
    """
    lesk = get_lesk()
    if lesk is None:
        return None

    context = [t.text for t in token.doc]
    try:
        return lesk(context, lemma, wordnet_pos)
    except Exception:
        # Lesk reaches into WordNet for every context word; a missing corpus or
        # an unparseable token should skip disambiguation, not fail the repair.
        return None


def _candidate_replacements(token, table: FrequencyTable) -> list[Candidate]:
    """Higher-frequency near-synonyms for `token` in context, best first.

    Candidates come from WordNet, not vector search: nearest neighbours in
    embedding space include antonyms and co-hyponyms, which would silently
    invert meaning.

    Every candidate is a lemma of a synset the original word belongs to, so
    synonymy holds by construction. Synonymy is not substitutability, though:
    the sense has to be the one the sentence is actually using. Sense selection
    is therefore, in order of preference, the sense Lesk resolves from context,
    or else WordNet's dominant sense. It is never the unrestricted
    frequency-ordered list -- that is what let "a deliberate attempt" reach
    "careful" (sense 1) and "favorable terms" reach "lucky" (sense 2).
    """
    wordnet = get_wordnet()
    if wordnet is None:
        return []

    wordnet_pos = _WORDNET_POS.get(token.pos_)
    if wordnet_pos is None:
        return []

    lemma = token.lemma_.lower()
    all_synsets = wordnet.synsets(lemma, pos=wordnet_pos)
    if not all_synsets:
        return []

    # The dominant sense is the reference point for the drift check below, even
    # when it is not the sense being substituted from.
    primary = all_synsets[0]

    resolved = _disambiguate_sense(token, lemma, wordnet_pos)
    if resolved is not None:
        selected = [resolved]
        disambiguated = True
    else:
        selected = all_synsets[: config.MAX_SYNSETS_PER_WORD]
        disambiguated = False

    original_logfreq = table.logfreq(token.text)
    seen = set()
    candidates: list[Candidate] = []

    for synset in selected:
        # Checked per synset rather than per candidate: sense drift is a
        # property of the sense, not of the individual lemma inside it.
        sense_similarity = _sense_similarity(wordnet, primary, synset)
        if (
            sense_similarity is None
            or sense_similarity < config.MIN_SENSE_SIMILARITY
        ):
            continue

        label = _sense_label(synset)
        for name in synset.lemma_names():
            candidate = name.lower()
            if "_" in candidate or not candidate.isalpha():
                continue
            if candidate == lemma or candidate in seen:
                continue
            seen.add(candidate)

            gain = table.logfreq(candidate) - original_logfreq
            if gain < config.MIN_FREQ_GAIN:
                continue

            candidates.append(
                Candidate(candidate, gain, sense_similarity, disambiguated, label)
            )

    # Sense proximity outranks frequency gain: an easier word that means
    # something else is a worse outcome than a hard word left in place. Within
    # a single sense every candidate ties, so gain breaks the tie.
    candidates.sort(key=lambda item: (item.sense_similarity, item.gain), reverse=True)
    candidates = candidates[: config.MAX_SUBSTITUTION_CANDIDATES]
    return _filter_by_sentence_similarity(token, candidates, table)


def _filter_by_sentence_similarity(token, candidates, table: FrequencyTable):
    """Drop lemmas that are synonyms on paper but not in this context.

    WordNet cannot tell "postpone the merger" from "table the merger". The
    encoder can, once both words sit in the same local window. One encode
    call covers the original window plus every surviving candidate.
    """
    if not candidates:
        return candidates

    surfaces = []
    viable = []
    for candidate in candidates:
        replacement = _realised_replacement(candidate.word, token, table)
        if replacement is None:
            continue
        viable.append(candidate)
        surfaces.append(_context_window(token, replacement))

    if not viable:
        return []

    encoder = get_sentence_encoder()
    vectors = encoder.encode(
        [_context_window(token), *surfaces], normalize_embeddings=True
    )
    original = vectors[0]
    floor = config.MIN_SUBSTITUTION_SENTENCE_SIMILARITY
    return [
        candidate
        for candidate, vector in zip(viable, vectors[1:])
        if float(original @ vector) >= floor
    ]


def _hard_words(docs, table: FrequencyTable, protected: set[str]):
    """Substitutable content words, hardest first."""
    targets = []
    for sentence_index, doc in enumerate(docs):
        for token in doc:
            if not token.is_alpha or token.is_stop:
                continue
            if token.pos_ not in _WORDNET_POS:
                continue
            if token.tag_ not in _INFLECTABLE_TAGS:
                continue
            # protected_terms is a hard constraint, not a preference.
            if token.lower_ in protected or token.lemma_.lower() in protected:
                continue
            if table.is_proper(token.text):
                continue
            logfreq = table.logfreq(token.text)
            if logfreq > config.HARD_WORD_LOGFREQ_THRESHOLD:
                continue
            targets.append((logfreq, sentence_index, token.i, token))

    targets.sort(key=lambda item: item[0])
    return targets


def _substitute_once(
    sentences,
    docs,
    table: FrequencyTable,
    protected: set[str],
    introduced: set[str],
):
    """Replace the hardest remaining substitutable word. Returns an edit or None.

    Mutates `sentences` and `docs` in place on success: `docs` is refreshed only
    for the sentence that changed, so token offsets stay valid for the next call
    without re-parsing the whole passage.
    """
    for _, sentence_index, _, token in _hard_words(docs, table, protected):
        # A word this run already introduced is never substituted again.
        # Chaining compounds sense drift one defensible hop at a time:
        # "tenacity" -> "persistence" -> "continuity" is two reasonable-looking
        # edits that together assert something the passage never said.
        if token.lower_ in introduced:
            continue

        for candidate in _candidate_replacements(token, table):
            replacement = _realised_replacement(candidate.word, token, table)
            if replacement is None:
                continue

            original_sentence = sentences[sentence_index]
            rewritten = (
                original_sentence[: token.idx]
                + replacement
                + original_sentence[token.idx + len(token.text) :]
            )

            # Same part of speech in context, verified by re-parsing.
            reparsed = get_nlp()(rewritten)
            matching = [t for t in reparsed if t.idx == token.idx]
            if not matching or matching[0].pos_ != token.pos_:
                continue

            sentences[sentence_index] = rewritten
            docs[sentence_index] = reparsed
            introduced.add(replacement.lower())

            sense_path = (
                f"sense: {candidate.sense_label}"
                if candidate.disambiguated
                else f"dominant sense, not disambiguated: {candidate.sense_label}"
            )
            return RepairEdit(
                operator=OPERATOR_SUBSTITUTE,
                sentence_index=sentence_index,
                before=token.text,
                after=replacement,
                rationale=(
                    f"Replaced '{token.text}' ({sense_path}) with "
                    f"'{replacement}' (frequency gain {candidate.gain:.2f}, "
                    f"sense similarity {candidate.sense_similarity:.2f})."
                ),
                sense_disambiguated=candidate.disambiguated,
            )
    return None


def substitution_targets(sentences, table: FrequencyTable, protected: set[str]):
    """(sentence_index, token) for every word Operator B would consider.

    Hardest first, same ordering the operator itself uses. Exposed so
    diagnostics can describe what the operator was working with without
    reimplementing the selection rules.
    """
    docs = [get_nlp()(sentence) for sentence in sentences]
    return [(index, token) for _, index, _, token in _hard_words(docs, table, protected)]


def replacement_options(token, table: FrequencyTable) -> list[Candidate]:
    """Candidates Operator B would consider for `token`, best first."""
    return _candidate_replacements(token, table)


def resolved_sense(token):
    """The WordNet sense Operator B reads `token` as, or None if unresolved.

    None means Lesk could not disambiguate from context and the operator fell
    back to the dominant sense.
    """
    wordnet = get_wordnet()
    if wordnet is None:
        return None

    wordnet_pos = _WORDNET_POS.get(token.pos_)
    if wordnet_pos is None:
        return None

    return _disambiguate_sense(token, token.lemma_.lower(), wordnet_pos)


def _apply_substitution(
    sentences,
    table: FrequencyTable,
    protected: set[str],
    introduced: set[str] | None = None,
    batch_size: int | None = None,
):
    """Replace up to `batch_size` hard words, hardest first.

    Returns a list of edits, one per substitution, empty when nothing applied.
    `introduced` carries the words earlier passes already substituted in, which
    are off limits; it is extended in place with the words this batch adds.

    Substitutions come in batches because each one is a weak lever: the score
    averages over every content word, so a single replacement with a strong
    frequency gain moves the estimate by only a few hundredths of a grade. One
    full-passage re-score per edit was paying parse costs out of proportion to
    the movement bought. The batch shares one re-score; within it, only the
    sentence just edited is re-parsed.
    """
    limit = config.SUBSTITUTION_BATCH_SIZE if batch_size is None else batch_size
    docs = [get_nlp()(sentence) for sentence in sentences]
    edits: list[RepairEdit] = []
    introduced = set() if introduced is None else introduced

    while len(edits) < limit:
        edit = _substitute_once(sentences, docs, table, protected, introduced)
        if edit is None:
            break
        edits.append(edit)

    return edits


# --------------------------------------------------------------------------
# Operator C: clause simplification (off by default)
# --------------------------------------------------------------------------


def _try_passive_to_active(doc) -> str | None:
    """Rewrite a by-agent passive as active voice, when it is safe to do so.

    Only regular verbs qualify: if the lemma plus the regular -ed rule does not
    reproduce the observed participle, the verb is irregular and is left alone
    rather than conjugated incorrectly.
    """
    for token in doc:
        if token.dep_ != "agent" or token.lower_ != "by":
            continue

        agents = [child for child in token.children if child.dep_ == "pobj"]
        if not agents:
            continue

        verb = token.head
        if verb.tag_ != "VBN":
            continue
        if _past_tense(verb.lemma_) != verb.lower_:
            continue

        subjects = [child for child in verb.children if child.dep_ == "nsubjpass"]
        auxiliaries = [child for child in verb.children if child.dep_ == "auxpass"]
        if not subjects or not auxiliaries:
            continue

        aux = auxiliaries[0].lower_
        if aux in {"was", "were"}:
            active_verb = _past_tense(verb.lemma_)
        elif aux in {"is", "are"}:
            active_verb = _pluralise(verb.lemma_) if aux == "is" else verb.lemma_
        else:
            continue

        agent_phrase = "".join(t.text_with_ws for t in agents[0].subtree).strip()
        object_phrase = "".join(t.text_with_ws for t in subjects[0].subtree).strip()
        return _tidy(f"{agent_phrase} {active_verb} {object_phrase}")

    return None


def _apply_simplification(sentences):
    """Remove parenthetical asides, then try a safe passive-to-active rewrite."""
    for index, sentence in enumerate(sentences):
        stripped = _PARENTHETICAL.sub("", sentence)
        if stripped != sentence and stripped.strip():
            sentences[index] = _tidy(stripped)
            return RepairEdit(
                operator=OPERATOR_SIMPLIFY,
                sentence_index=index,
                before=sentence,
                after=sentences[index],
                rationale="Removed a parenthetical aside.",
            )

    for index, sentence in enumerate(sentences):
        rewritten = _try_passive_to_active(get_nlp()(sentence))
        if rewritten and rewritten != sentence:
            sentences[index] = rewritten
            return RepairEdit(
                operator=OPERATOR_SIMPLIFY,
                sentence_index=index,
                before=sentence,
                after=rewritten,
                rationale="Converted passive voice to active.",
            )
    return None


# --------------------------------------------------------------------------
# Control loop
# --------------------------------------------------------------------------


def _preserved_terms(text: str, protected: list[str]) -> list[str]:
    lowered = text.lower()
    return [term for term in protected if term.lower() in lowered]


def _band_distance(score, band: bands.Band) -> float:
    """How far a score sits outside the band. Zero when inside."""
    if score.estimated_grade > band.high:
        return score.estimated_grade - band.high
    if score.estimated_grade < band.low:
        return band.low - score.estimated_grade
    return 0.0


def _trial_edit(sentences, apply_operator, table: FrequencyTable):
    """Apply an operator to a copy of `sentences` and score the result.

    Returns (trial_sentences, edits, score) or None when the operator has
    nothing to do. The caller keeps whichever trial it prefers, so operators
    never mutate the live sentence list during selection.

    An operator returns a list because substitution applies a batch per pass.
    A batch is one trial: it competes against the splitting trial on the score
    it reaches, which is what makes the comparison between a strong single
    split and several weak substitutions a fair one.
    """
    trial = list(sentences)
    edits = apply_operator(trial)
    if not edits:
        return None
    return trial, edits, score_text(" ".join(trial), table=table)


def _as_edits(edit: RepairEdit | None) -> list[RepairEdit]:
    """Adapt a single-edit operator to the list protocol `_trial_edit` expects."""
    return [] if edit is None else [edit]


def _effective_max_iterations(
    initial_grade: float, band: bands.Band, override: int | None
) -> int:
    """Control-loop passes to allow, scaled to the size of the gap to close.

    The gap is measured to the near edge of the band rather than its center,
    because entering the band is what the loop halts on -- budgeting to the
    center would over-provision every run by half a band width.
    """
    if override is not None:
        return max(0, override)

    gap = max(0.0, initial_grade - band.high)
    scaled = math.ceil(gap * config.BASE_ITERATIONS_PER_GRADE_GAP)
    return min(max(config.MAX_ITERATIONS, scaled), config.ITERATION_HARD_CEILING)


def measured_without_repair(text, target_grade, *, table=None, protected_terms=None):
    """Score `text` without running Operators A–C.

    Used by single_pass_llm so Operator D still gets a real PassageDiagnostics
    from the first deterministic pass, without the iterative substitution /
    split loop. score_text is still the grade; this is not an unscored skip.
    """
    table = table if table is not None else get_default_table()
    band = bands.target_band(target_grade)
    protected = list(protected_terms or [])
    initial_score = score_text(text, table=table)
    in_band = bands.in_band(initial_score, band)
    if initial_score.features.word_count == 0:
        reason = REASON_EMPTY
    elif in_band:
        reason = None
    elif initial_score.estimated_grade < band.low:
        reason = REASON_BELOW_BAND
    else:
        reason = REASON_ABOVE_BAND
    return CorrectionResult(
        text=text,
        original_text=text,
        final_score=initial_score,
        initial_score=initial_score,
        target_band=band,
        in_band=in_band,
        iterations=0,
        edits=[],
        protected_terms_preserved=_preserved_terms(text, protected),
        gate_passed=in_band,
        failure_reason=reason,
        score_trajectory=[],
    )


def correct_text(
    text: str,
    target_grade: int | str,
    *,
    protected_terms: list[str] | None = None,
    max_iterations: int | None = None,
    table: FrequencyTable | None = None,
) -> CorrectionResult:
    """Bring `text` into the band for `target_grade`, or explain why it cannot be.

    Deterministic operators only simplify, so text harder than target is
    repaired here and text easier than target is reported as below-band.
    Raising difficulty is Operator D's job, in ai_rewrite.

    `max_iterations` defaults to a budget scaled to the size of the gap. Passing
    a value overrides that budget exactly.
    """
    table = table if table is not None else get_default_table()
    band = bands.target_band(target_grade)
    protected = list(protected_terms or [])
    protected_lower = {term.lower() for term in protected}

    initial_score = score_text(text, table=table)

    if initial_score.features.word_count == 0:
        return CorrectionResult(
            text=text,
            original_text=text,
            final_score=initial_score,
            initial_score=initial_score,
            target_band=band,
            in_band=False,
            iterations=0,
            gate_passed=False,
            failure_reason=REASON_EMPTY,
        )

    iteration_budget = _effective_max_iterations(
        initial_score.estimated_grade, band, max_iterations
    )

    sentences = [doc.text for doc in parse_sentences(text)]
    edits: list[RepairEdit] = []
    trajectory: list[float] = []
    introduced_words: set[str] = set()
    score = initial_score
    iterations = 0
    failure_reason: str | None = None

    while True:
        if bands.in_band(score, band):
            break

        if score.estimated_grade < band.low:
            failure_reason = REASON_BELOW_BAND
            break

        if iterations >= iteration_budget:
            failure_reason = REASON_MAX_ITERATIONS
            break

        # Both operators are offered every iteration. Hard-prioritising
        # splitting starved substitution completely on passages that need both:
        # a split candidate exists on nearly every iteration of a long passage,
        # so the budget was spent before substitution was ever attempted.
        # _apply_split self-guards on sentence length, so calling it is safe
        # even when nothing is over the ceiling.
        trials = []
        for operator, apply_operator in (
            (OPERATOR_SPLIT, lambda s: _as_edits(_apply_split(s, band))),
            (
                OPERATOR_SUBSTITUTE,
                # A copy, because a trial that loses the comparison must not
                # burn words the winning trial never used.
                lambda s: _apply_substitution(
                    s, table, protected_lower, set(introduced_words)
                ),
            ),
        ):
            trial = _trial_edit(sentences, apply_operator, table)
            if trial is not None:
                trials.append((operator, trial))

        if not trials and config.ENABLE_CLAUSE_SIMPLIFICATION:
            trial = _trial_edit(
                sentences, lambda s: _as_edits(_apply_simplification(s)), table
            )
            if trial is not None:
                trials.append((OPERATOR_SIMPLIFY, trial))

        if not trials:
            failure_reason = REASON_NO_EDITS
            break

        # Prefer whichever edit lands closest to the band. On a tie, prefer the
        # operator that did not run last iteration, so neither axis is starved.
        previous_operator = edits[-1].operator if edits else None
        operator, (chosen_sentences, chosen_edits, projected) = min(
            trials,
            key=lambda item: (
                _band_distance(item[1][2], band),
                item[0] == previous_operator,
            ),
        )

        rejected = [
            f"{other} would reach {trial[2].estimated_grade:.2f}"
            for other, trial in trials
            if other != operator
        ]
        # A batch shares one selection rationale: the operators competed once,
        # on the score the whole batch reaches.
        selection = (
            f"Chose {operator} ({len(chosen_edits)} edit"
            f"{'' if len(chosen_edits) == 1 else 's'}): projected grade "
            f"{projected.estimated_grade:.2f} against a target of "
            f"{band.low:.1f}-{band.high:.1f}"
            + (f"; {', '.join(rejected)}." if rejected else "; no alternative applied.")
        )
        for edit in chosen_edits:
            edit.selection = selection

        sentences[:] = chosen_sentences
        introduced_words.update(
            edit.after.lower()
            for edit in chosen_edits
            if edit.operator == OPERATOR_SUBSTITUTE
        )
        edits.extend(chosen_edits)
        iterations += 1
        score = projected
        trajectory.append(score.estimated_grade)

    final_text = " ".join(sentences)
    is_in_band = bands.in_band(score, band)

    if not is_in_band and failure_reason is None:
        failure_reason = REASON_ABOVE_BAND

    return CorrectionResult(
        text=final_text,
        original_text=text,
        final_score=score,
        initial_score=initial_score,
        target_band=band,
        in_band=is_in_band,
        iterations=iterations,
        edits=edits,
        protected_terms_preserved=_preserved_terms(final_text, protected),
        gate_passed=is_in_band,
        failure_reason=None if is_in_band else failure_reason,
        score_trajectory=trajectory,
    )
