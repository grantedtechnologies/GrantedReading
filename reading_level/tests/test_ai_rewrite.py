"""Operator D orchestration.

The model is mocked throughout: nothing here touches the network. The point of
these tests is that the deterministic gates decide, and the model never does.
"""

import json
import logging
from types import SimpleNamespace

import pytest
from drift_cases import (
    DRIFT_FACTS_INVERTED,
    INFORMATIONAL_FACT_KEPT,
    INFORMATIONAL_NUMBER_ALTERED,
    LEGIT_FULL_REWORD,
    LEGIT_MILD_REWORD,
    NARRATIVE_NEW_ENDING,
)

import ai
import ai_rewrite
from reading_level import (
    ReadingLevelError,
    bands as rl_bands,
    config as rl_config,
    correct_text,
    score_text,
)


def _rewrite_json(passage):
    return json.dumps({"passage": passage})


@pytest.fixture
def failing_correction(fixture_text, table):
    """A real deterministic run that could not reach the band."""
    result = correct_text(fixture_text("business_register"), 6, table=table)
    assert not result.gate_passed
    return result


def _recorder(*responses):
    """A stand-in for generate_text that returns canned answers in order."""
    calls = []

    def generate(prompt):
        calls.append(prompt)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    return generate, calls


# --------------------------------------------------------------------------
# Gate behaviour
# --------------------------------------------------------------------------


def test_a_rewrite_in_band_is_accepted(failing_correction, table):
    generate, calls = _recorder(_rewrite_json(LEGIT_MILD_REWORD))

    outcome = ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, table=table
    )

    assert outcome.gate_passed
    assert outcome.llm_rewrite_applied
    assert outcome.text == LEGIT_MILD_REWORD
    assert outcome.llm_attempts == 1
    assert len(calls) == 1


def test_a_rewrite_that_stays_out_of_band_is_rejected(failing_correction, table):
    """Gate 1. The model does not get to say whether it succeeded."""
    still_hard = failing_correction.text
    generate, calls = _recorder(_rewrite_json(still_hard))

    outcome = ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, table=table
    )

    assert not outcome.gate_passed
    assert not outcome.llm_rewrite_applied
    assert outcome.failure_reason == "rewrite_rejected_out_of_band"


def test_an_overcorrected_rewrite_is_rejected(failing_correction, table):
    """Below the band floor is its own failure, not a bonus."""
    generate, _ = _recorder(_rewrite_json(LEGIT_FULL_REWORD))

    outcome = ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, table=table
    )

    assert not outcome.gate_passed


def test_the_deterministic_result_survives_a_rejected_rewrite(
    failing_correction, table
):
    generate, _ = _recorder(_rewrite_json(failing_correction.text))

    outcome = ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, table=table
    )

    assert not outcome.gate_passed
    assert not outcome.llm_rewrite_applied
    assert outcome.text == failing_correction.text
    assert outcome.edits == failing_correction.edits
    assert outcome.score_trajectory == failing_correction.score_trajectory
    assert outcome.rewrite_attempts
    assert outcome.rewrite_attempts[0].in_band is False


def test_the_corrector_keeps_the_closest_out_of_band_draft(
    failing_correction, table
):
    """A teacher should see the rewrite, not only the deterministic leftover."""
    generate, _ = _recorder(_rewrite_json(failing_correction.text))

    outcome = ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, table=table,
        keep_closest=True,
    )

    assert not outcome.gate_passed
    assert outcome.llm_rewrite_applied
    assert outcome.text == failing_correction.text
    assert outcome.failure_reason == "rewrite_rejected_out_of_band"


# --------------------------------------------------------------------------
# Budget and retry
# --------------------------------------------------------------------------


def test_a_rejected_attempt_is_retried_with_feedback(failing_correction, table):
    generate, calls = _recorder(
        _rewrite_json(failing_correction.text), _rewrite_json(LEGIT_MILD_REWORD)
    )

    outcome = ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, max_attempts=2, table=table
    )

    assert outcome.gate_passed
    assert outcome.llm_attempts == 2
    assert "rejected because" in calls[1]


def test_overshoot_feedback_asks_to_restore_register(failing_correction, table):
    """A too-easy draft should not be told to cut more rare words."""
    generate, calls = _recorder(
        _rewrite_json(LEGIT_FULL_REWORD), _rewrite_json(LEGIT_MILD_REWORD)
    )

    ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, max_attempts=2, table=table
    )

    assert "too easy" in calls[1]
    assert "Put back some mid-level content words" in calls[1]
    assert "Cut rare content words" not in calls[1]


def test_attempts_are_capped(failing_correction, table):
    generate, calls = _recorder(_rewrite_json(failing_correction.text))

    outcome = ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, max_attempts=2, table=table
    )

    assert len(calls) == 2
    assert outcome.llm_attempts == 2
    assert not outcome.gate_passed


def test_the_default_budget_is_max_generation_attempts(failing_correction, table):
    generate, calls = _recorder(_rewrite_json(failing_correction.text))

    ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, table=table
    )

    assert len(calls) == rl_config.MAX_GENERATION_ATTEMPTS


def test_a_failing_model_call_falls_back_instead_of_propagating(
    failing_correction, table
):
    def generate(prompt):
        raise RuntimeError("provider is down")

    outcome = ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, table=table
    )

    assert not outcome.gate_passed
    assert outcome.failure_reason == "rewrite_call_failed"
    assert outcome.text == failing_correction.text


def test_an_empty_rewrite_is_retried(failing_correction, table):
    generate, calls = _recorder(_rewrite_json(""), _rewrite_json(LEGIT_MILD_REWORD))

    outcome = ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, max_attempts=2, table=table
    )

    assert outcome.gate_passed
    assert len(calls) == 2


# --------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------


def test_the_prompt_carries_the_diagnosis(failing_correction, table):
    generate, calls = _recorder(_rewrite_json(LEGIT_MILD_REWORD))

    ai_rewrite.escalate_to_rewrite(
        failing_correction,
        6,
        generate=generate,
        protected_terms=["counterparties"],
        table=table,
    )
    prompt = calls[0]

    assert "Grade 6" in prompt
    assert "counterparties" in prompt
    assert "reading-level editor" in prompt
    assert failing_correction.text in prompt


def test_the_prompt_defines_easier_words_and_assigns_the_listed_edits(
    failing_correction, table
):
    """The second pass is an edit list, not a free rewrite."""
    generate, calls = _recorder(_rewrite_json(LEGIT_MILD_REWORD))

    ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, table=table
    )
    prompt = calls[0]

    assert "What counts as an easier word" in prompt
    assert "Replace \"" in prompt or "Hard words" in prompt
    assert "Do not regenerate the passage from scratch" in prompt
    assert "only two numbers that move it" in prompt
    assert "Going below the floor is as wrong as staying above the ceiling" in prompt
    assert "Aim near the middle" in prompt
    assert "average" in prompt.lower()


def test_a_large_syntax_gap_names_the_average_sentence_target(fixture_text, table):
    """Grade 6 garden prose targeting 4 is a sentence-length problem."""
    correction = correct_text(fixture_text("medium_upper_elementary"), 4, table=table)
    generate, calls = _recorder(_rewrite_json(LEGIT_MILD_REWORD))

    ai_rewrite.escalate_to_rewrite(
        correction, 4, generate=generate, table=table
    )
    prompt = calls[0]

    assert "Sentences currently average" in prompt
    assert "they need to average about" in prompt
    assert "Sentence length is the main leftover lever" in prompt or (
        "No sentence is over" in prompt
    )


def test_too_hard_feedback_names_the_average_when_sentences_are_leftover(
    fixture_text, table
):
    """A timid 6→4 edit must be told to split, not to swap a few more nouns."""
    correction = correct_text(fixture_text("medium_upper_elementary"), 4, table=table)
    still_long = fixture_text("medium_upper_elementary")
    generate, calls = _recorder(
        _rewrite_json(still_long), _rewrite_json(LEGIT_MILD_REWORD)
    )

    ai_rewrite.escalate_to_rewrite(
        correction, 4, generate=generate, max_attempts=2, table=table
    )

    assert "Sentences average" in calls[1]
    assert "they need to average about" in calls[1]


@pytest.mark.parametrize(
    "passage_type", ["narrative", "informational"], ids=["narrative", "informational"]
)
def test_the_prompt_forbids_flipping_a_negation_in_both_tiers(
    failing_correction, table, passage_type
):
    """Its own line in the prompt, not a clause inside topic preservation.

    Narrative freedom extends to changing what happens; it does not extend to
    reversing meaning halfway through a passage. The negation-delta gate exists
    to catch that, and the generation should be avoiding it rather than
    relying on being caught.
    """
    generate, calls = _recorder(_rewrite_json(LEGIT_MILD_REWORD))

    ai_rewrite.escalate_to_rewrite(
        failing_correction,
        6,
        generate=generate,
        table=table,
        passage_type=passage_type,
    )

    assert "Never invert a fact or flip a negation" in calls[0]


@pytest.mark.parametrize(
    "passage_type", ["narrative", "informational"], ids=["narrative", "informational"]
)
def test_protected_terms_stay_hard_in_both_tiers(fixture_text, table, passage_type):
    correction = correct_text(
        fixture_text("business_register"), 6, protected_terms=["counterparties"],
        table=table,
    )
    generate, calls = _recorder(_rewrite_json(LEGIT_MILD_REWORD))

    ai_rewrite.escalate_to_rewrite(
        correction,
        6,
        generate=generate,
        protected_terms=["counterparties"],
        table=table,
        passage_type=passage_type,
    )

    assert "Keep every protected term exactly as written" in calls[0]
    assert "counterparties" in calls[0]


def test_the_narrative_tier_grants_structural_freedom(failing_correction, table):
    generate, calls = _recorder(_rewrite_json(LEGIT_MILD_REWORD))

    ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, table=table,
        passage_type="narrative",
    )
    prompt = calls[0]

    assert "NARRATIVE passage" in prompt
    assert "the ending" in prompt
    assert "Apply the listed word and sentence edits first" in prompt
    assert "Keep every fact the passage conveys" not in prompt


def test_the_informational_tier_pins_the_facts(failing_correction, table):
    generate, calls = _recorder(_rewrite_json(LEGIT_MILD_REWORD))

    ai_rewrite.escalate_to_rewrite(
        failing_correction, 6, generate=generate, table=table,
        passage_type="informational",
    )
    prompt = calls[0]

    assert "INFORMATIONAL passage" in prompt
    assert "Keep every fact the passage conveys" in prompt
    assert "Apply the listed word and sentence edits first" in prompt
    assert "the ending" not in prompt


# --------------------------------------------------------------------------
# Passage type: who decides how much the rewrite may change
# --------------------------------------------------------------------------


def test_an_unspecified_passage_type_warns_and_takes_the_strict_tier(
    failing_correction, table, caplog
):
    """Silence must not buy the permissive tier.

    A caller who forgets the flag gets the informational rules, because
    granting plot freedom to a passage whose facts a question set depends on
    fails in a way no gate here can see.
    """
    generate, calls = _recorder(_rewrite_json(LEGIT_MILD_REWORD))

    with caplog.at_level(logging.WARNING, logger="reading_level.diagnostics"):
        ai_rewrite.escalate_to_rewrite(
            failing_correction, 6, generate=generate, table=table
        )

    assert "no passage_type supplied" in caplog.text
    assert "INFORMATIONAL passage" in calls[0]


def test_an_unknown_passage_type_is_rejected_rather_than_coerced(
    failing_correction, table
):
    """A typo at the call site must not silently resolve to a tier."""
    generate, _ = _recorder(_rewrite_json(LEGIT_MILD_REWORD))

    with pytest.raises(ValueError, match="passage_type"):
        ai_rewrite.escalate_to_rewrite(
            failing_correction, 6, generate=generate, table=table,
            passage_type="story",
        )


def test_a_narrative_rewrite_may_change_the_ending(fixture_text, table):
    """The behaviour change this loosening is for.

    The rewrite wins the audition the original lost and drops the fumbled
    notes entirely. Theme and protected term hold, so it ships.
    """
    correction = correct_text(
        fixture_text("narrative_recital"), 4, protected_terms=["violin"], table=table
    )
    assert not correction.gate_passed

    generate, _ = _recorder(_rewrite_json(NARRATIVE_NEW_ENDING))
    outcome = ai_rewrite.escalate_to_rewrite(
        correction,
        4,
        generate=generate,
        protected_terms=["violin"],
        table=table,
        passage_type="narrative",
    )

    assert outcome.gate_passed
    assert outcome.llm_rewrite_applied
    assert outcome.text == NARRATIVE_NEW_ENDING


def test_an_informational_rewrite_may_reorganise_but_keeps_the_fact(
    fixture_text, table
):
    """Structural freedom applies; the number a question asks about does not."""
    correction = correct_text(
        fixture_text("informational_boiling"), 5,
        protected_terms=["evaporation"], table=table,
    )
    assert not correction.gate_passed

    generate, calls = _recorder(_rewrite_json(INFORMATIONAL_FACT_KEPT))
    outcome = ai_rewrite.escalate_to_rewrite(
        correction,
        5,
        generate=generate,
        protected_terms=["evaporation"],
        table=table,
        passage_type="informational",
    )

    assert outcome.gate_passed
    assert "100 degrees Celsius" in outcome.text
    # The instruction the tier turns on, present in the prompt that produced it.
    assert "Comprehension questions are built on them" in calls[0]


def test_an_in_band_rewrite_ships_even_if_a_number_changes(
    fixture_text, table
):
    """Meaning verification is off: grade is the only gate.

    The informational prompt still tells the model to keep the fact. Nothing
    currently rejects the rewrite if it does not.
    """
    correction = correct_text(
        fixture_text("informational_boiling"), 5,
        protected_terms=["evaporation"], table=table,
    )
    generate, _ = _recorder(_rewrite_json(INFORMATIONAL_NUMBER_ALTERED))

    outcome = ai_rewrite.escalate_to_rewrite(
        correction,
        5,
        generate=generate,
        protected_terms=["evaporation"],
        table=table,
        passage_type="informational",
    )

    assert outcome.gate_passed
    assert outcome.text == INFORMATIONAL_NUMBER_ALTERED


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"passage": "Hello there."}', "Hello there."),
        ('```json\n{"passage": "Hello there."}\n```', "Hello there."),
        ('Sure! {"passage": "Hello there."} Hope that helps.', "Hello there."),
        ("Hello there.", "Hello there."),
    ],
    ids=["json", "fenced", "chatty", "bare"],
)
def test_rewrite_output_parsing(raw, expected):
    assert ai_rewrite.parse_rewrite_output(raw) == expected


# --------------------------------------------------------------------------
# ai.py orchestration: who raises and who does not
# --------------------------------------------------------------------------


@pytest.fixture
def mock_generate(monkeypatch):
    def _install(*responses):
        generate, calls = _recorder(*responses)
        monkeypatch.setattr(ai, "generate_text", generate)
        return calls

    return _install


def _worksheet_json(story):
    return json.dumps(
        {
            "title": "A Story",
            "story": story,
            "questions": {f"q{i}": f"Question {i}?" for i in range(1, 6)},
        }
    )


def test_operator_d_does_not_fire_when_correction_succeeds(
    mock_generate, fixture_text
):
    """The cheap path is fast, free, and auditable; do not spend a call on it."""
    calls = mock_generate(_worksheet_json(fixture_text("easy_primary")))

    result = ai.generate_leveled_passage("a dog named Sam", 2)

    assert result["reading_level"]["in_band"]
    assert result["llm_rewrite_applied"] is False
    assert len(calls) == 1


def test_operator_d_fires_on_below_band_text(mock_generate, fixture_text):
    """A too-easy draft gets the same rewrite pass as a too-hard one."""
    in_band_for_nine = fixture_text("informational_boiling")
    calls = mock_generate(
        _worksheet_json(fixture_text("easy_primary")),
        _rewrite_json(in_band_for_nine),
    )

    result = ai.generate_leveled_passage("a dog named Sam", 9)

    assert result["llm_rewrite_applied"] is True
    assert result["reading_level"]["in_band"]
    assert len(calls) == 2
    assert "too low" in calls[1]


def test_a_too_easy_passage_gets_a_raise_difficulty_prompt(fixture_text, table):
    correction = correct_text(fixture_text("easy_primary"), 9, table=table)
    generate, calls = _recorder(_rewrite_json(fixture_text("easy_primary")))

    ai_rewrite.escalate_to_rewrite(
        correction, 9, generate=generate, table=table
    )

    assert "too low" in calls[0]
    assert "What counts as a harder word" in calls[0]
    assert "What counts as a longer sentence" in calls[0]
    assert "grade levels too high" not in calls[0]


def test_operator_d_fires_when_correction_fails(mock_generate, fixture_text):
    calls = mock_generate(
        _worksheet_json(fixture_text("business_register")),
        _rewrite_json(LEGIT_MILD_REWORD),
    )

    result = ai.generate_leveled_passage("a negotiation", 6)

    assert result["llm_rewrite_applied"] is True
    assert result["passage"] == LEGIT_MILD_REWORD
    assert len(calls) == 2
    # Generation asks the model for a story, so the rewrite gets the narrative
    # tier without the caller having to say so.
    assert "NARRATIVE passage" in calls[1]


def test_generation_path_raises_when_the_rewrite_stays_out_of_band(
    mock_generate, fixture_text
):
    """A worksheet goes to a student; a visible error beats a wrong level."""
    still_hard = fixture_text("business_register")
    mock_generate(
        _worksheet_json(still_hard),
        _rewrite_json(still_hard),
    )

    with pytest.raises(ReadingLevelError) as excinfo:
        ai.generate_leveled_passage("a negotiation", 6)

    assert excinfo.value.reason == "rewrite_rejected_out_of_band"


def test_total_model_calls_stay_within_the_cap(mock_generate, fixture_text):
    still_hard = fixture_text("business_register")
    calls = mock_generate(
        _worksheet_json(still_hard),
        _rewrite_json(still_hard),
    )

    with pytest.raises(ReadingLevelError):
        ai.generate_leveled_passage("a negotiation", 6)

    assert len(calls) == rl_config.MAX_GENERATION_ATTEMPTS


def test_corrector_path_returns_a_flagged_result_instead_of_raising(
    mock_generate, fixture_text
):
    """A teacher is reading this one, so a partial improvement is useful."""
    mock_generate(_rewrite_json(fixture_text("business_register")))

    outcome = ai.correct_with_rewrite(
        fixture_text("business_register"), 6, allow_rewrite=True
    )

    assert not outcome.gate_passed
    assert outcome.failure_reason
    assert outcome.text
    assert outcome.edits
    assert outcome.rewrite_attempts


def test_corrector_path_escalates_text_that_is_too_easy(
    mock_generate, fixture_text
):
    calls = mock_generate(_rewrite_json(fixture_text("informational_boiling")))

    outcome = ai.correct_with_rewrite(
        fixture_text("easy_primary"), 9, allow_rewrite=True
    )

    assert calls
    assert "too low" in calls[0]
    assert "What counts as a harder word" in calls[0]
    assert outcome.llm_rewrite_applied is True


def test_rewrite_still_above_band_gets_another_repair_pass(monkeypatch):
    band = rl_bands.target_band(3)
    outcome = ai_rewrite.RewriteOutcome(
        text="still too hard",
        correction=SimpleNamespace(target_band=band, edits=[]),
        final_score=SimpleNamespace(estimated_grade=3.66),
        gate_passed=False,
        failure_reason="rewrite_rejected_out_of_band",
        llm_rewrite_applied=True,
    )
    called = {}

    def fake_correct(text, target, **kwargs):
        called["text"] = text
        return SimpleNamespace(
            text="nicked into band",
            final_score=SimpleNamespace(estimated_grade=3.2),
            gate_passed=True,
        )

    monkeypatch.setattr(ai, "correct_text", fake_correct)
    result = ai._polish_rewrite_with_repair(outcome, 3, [])
    assert called["text"] == "still too hard"
    assert result.gate_passed
    assert result.text == "nicked into band"
    assert result.failure_reason is None


def test_corrector_path_is_deterministic_unless_asked(mock_generate, fixture_text):
    calls = mock_generate(_rewrite_json(LEGIT_MILD_REWORD))

    outcome = ai.correct_with_rewrite(fixture_text("business_register"), 6)

    assert calls == []
    assert outcome.llm_rewrite_applied is False


def test_focus_vocabulary_is_parsed_and_asked_for_several_times():
    assert ai.parse_focus_vocabulary("  resilient, Resilient, photosynthesis ") == [
        "resilient",
        "photosynthesis",
    ]
    assert ai.parse_focus_vocabulary("") == []

    prompt = ai.build_worksheet_prompt(
        "4", "3", "soccer", focus_vocabulary=["resilient"]
    )
    assert '"resilient"' in prompt
    assert "3 to 5 times" in prompt
    assert "easier synonym" in prompt


def test_focus_phonics_is_parsed_and_asked_for():
    assert ai.parse_focus_phonics("  TION — action, station ") == "tion"
    assert ai.parse_focus_phonics("ing") == "ing"
    assert ai.parse_focus_phonics("") == ""
    assert ai.parse_focus_phonics("a") == ""
    assert ai.words_matching_phonics(
        "The station mention of the action was a surprise.", "tion"
    ) == ["station", "mention", "action"]

    prompt = ai.build_worksheet_prompt("4", "3", "soccer", focus_phonics="tion")
    assert '"tion"' in prompt
    assert "3 to 5 different words" in prompt
    assert "letter pattern" in prompt


def test_dok_prompt_differs_by_level():
    """The DOK parameter must change the question prompt, not just a label."""
    story = "Leo asked Maya to wait. He took a deep breath, then walked over."
    prompts = {
        level: ai.build_questions_prompt(story, "The Walk", level)
        for level in (1, 2, 3, 4)
    }
    assert "Recall & Reproduction" in prompts[1]
    assert "explicitly stated in the text" in prompts[1]
    assert "Skill & Concept" in prompts[2]
    assert "connecting two or more pieces" in prompts[2]
    assert "Strategic Thinking" in prompts[3]
    assert "support their answer with evidence" in prompts[3]
    assert "Extended Thinking" in prompts[4]
    assert "connects the story to the student's own experience" in prompts[4]
    assert len({prompts[1], prompts[2], prompts[3], prompts[4]}) == 4
    assert all("Leo asked Maya to wait" in prompts[level] for level in prompts)
    assert "Who / What / When / Where / Why template" in prompts[1]
    story_prompt = ai.build_worksheet_prompt("4", "3", "soccer")
    assert "Who / What / When / Where / Why" in story_prompt
    assert "Who ...?" not in story_prompt
    assert ai.parse_dok_level(None) == 1
    assert ai.parse_dok_level("DOK 3") == 3


def test_questions_prompt_uses_count_and_custom_levels():
    story = "Leo asked Maya to wait."
    three = ai.build_questions_prompt(story, "The Walk", 2, question_count=3)
    assert "Write exactly 3 questions" in three
    assert '"q3": "...?"' in three
    assert '"q4"' not in three
    custom = ai.build_questions_prompt(
        story, "The Walk", 1, question_count=4, dok_levels=[1, 3]
    )
    assert "mixed DOK levels: DOK 1, DOK 3" in custom
    assert "Recall & Reproduction" in custom
    assert "Strategic Thinking" in custom
    assert "Write exactly 4 questions" in custom


def test_parse_questions_output_respects_count():
    raw = json.dumps({"questions": {"q1": "One?", "q2": "Two?", "q3": "Three extra?"}})
    parsed = ai.parse_questions_output(raw, question_count=2)
    assert parsed == {"q1": "One?", "q2": "Two?"}


def test_dok_level_does_not_change_the_story_or_the_leveler(monkeypatch):
    """Changing question demand cannot change passage generation or repair."""
    story_prompts = []
    leveler_stories = []
    story = "Sam ran to the park. The sun was hot."

    def fake_generate(prompt):
        if "DOK level" in prompt:
            level = "1" if "Recall & Reproduction" in prompt else "3"
            return json.dumps({
                "questions": {f"q{i}": f"DOK {level} question {i}?" for i in range(1, 6)}
            })
        story_prompts.append(prompt)
        return _worksheet_json(story)

    def fake_correct(text, target, **kwargs):
        leveler_stories.append(text)
        score = score_text(text)
        return SimpleNamespace(
            text=text,
            target_band=rl_bands.target_band(target),
            final_score=score,
            gate_passed=True,
            llm_rewrite_applied=False,
            failure_reason=None,
        )

    monkeypatch.setattr(ai, "generate_text", fake_generate)
    monkeypatch.setattr(ai, "correct_with_rewrite", fake_correct)
    monkeypatch.setattr(ai, "generate_image", lambda prompt: "img")
    monkeypatch.setattr(ai.file, "generate_worksheet_pdf", lambda payload: b"%PDF")

    first, *_ = ai.generate_full_worksheet("4", "3", "soccer", dok_level=1)
    second, *_ = ai.generate_full_worksheet("4", "3", "soccer", dok_level=3)

    assert len(story_prompts) == 2
    assert story_prompts[0] == story_prompts[1]
    assert "DOK" not in story_prompts[0]
    assert leveler_stories == [story, story]
    assert first["story"] == second["story"] == story
    assert first["questions"]["q1"] != second["questions"]["q1"]
    assert "DOK 1" in first["questions"]["q1"]
    assert "DOK 3" in second["questions"]["q1"]


def test_focus_phonics_protects_matching_words_from_the_leveler(monkeypatch):
    captured = {}

    def fake_correct(text, target, **kwargs):
        captured["protected_terms"] = kwargs.get("protected_terms")
        score = score_text(text)
        return SimpleNamespace(
            text=text,
            target_band=rl_bands.target_band(target),
            final_score=score,
            gate_passed=True,
            llm_rewrite_applied=False,
            failure_reason=None,
        )

    monkeypatch.setattr(ai, "correct_with_rewrite", fake_correct)
    monkeypatch.setattr(ai, "generate_text", lambda prompt: _worksheet_json(
        "The station mention of the action was a surprise."
    ))
    monkeypatch.setattr(ai, "generate_image", lambda prompt: "img")
    monkeypatch.setattr(ai.file, "generate_worksheet_pdf", lambda payload: b"%PDF")

    ai.generate_full_worksheet(
        "6", "4", "soccer", focus_vocabulary="resilient", focus_phonics="tion"
    )

    assert captured["protected_terms"] == [
        "resilient",
        "station",
        "mention",
        "action",
    ]


def test_focus_vocabulary_is_protected_from_the_leveler(monkeypatch, fixture_text):
    captured = {}

    def fake_correct(text, target, **kwargs):
        captured["protected_terms"] = kwargs.get("protected_terms")
        score = score_text(text)
        return SimpleNamespace(
            text=text,
            target_band=rl_bands.target_band(target),
            final_score=score,
            gate_passed=True,
            llm_rewrite_applied=False,
            failure_reason=None,
        )

    monkeypatch.setattr(ai, "correct_with_rewrite", fake_correct)
    monkeypatch.setattr(ai, "generate_text", lambda prompt: _worksheet_json(
        "The resilient girl stayed resilient when the game got hard."
    ))
    monkeypatch.setattr(ai, "generate_image", lambda prompt: "img")
    monkeypatch.setattr(ai.file, "generate_worksheet_pdf", lambda payload: b"%PDF")

    ai.generate_full_worksheet("6", "4", "soccer", focus_vocabulary="resilient")

    assert captured["protected_terms"] == ["resilient"]


def test_worksheet_generation_keeps_the_first_draft_and_levels_the_story(
    monkeypatch, fixture_text
):
    """The home page needs the raw draft so a reviewer can see the lift."""
    original = fixture_text("business_register")

    def fake_generate(prompt):
        if "reading-level editor" in prompt:
            return _rewrite_json(LEGIT_MILD_REWORD)
        return _worksheet_json(original)

    monkeypatch.setattr(ai, "generate_text", fake_generate)
    monkeypatch.setattr(ai, "generate_image", lambda prompt: "img")
    monkeypatch.setattr(ai.file, "generate_worksheet_pdf", lambda payload: b"%PDF")

    model_data, _pdf, _image, _raw, leveling = ai.generate_full_worksheet(
        "6", "6", "soccer"
    )

    assert leveling["first_draft"]["story"] == original
    assert model_data["story"] == LEGIT_MILD_REWORD
    assert leveling["improved"] is True
    assert leveling["llm_rewrite_applied"] is True
    assert leveling["first_draft"]["estimated_grade"] > leveling["final"]["estimated_grade"]
    assert leveling["first_draft"]["below_target"] is False
    assert leveling["target_band"] == "Grade 6"


def test_worksheet_generation_raises_a_too_easy_draft(
    monkeypatch, fixture_text
):
    original = fixture_text("easy_primary")
    raised = fixture_text("informational_boiling")

    def fake_generate(prompt):
        if "reading-level editor" in prompt:
            return _rewrite_json(raised)
        return _worksheet_json(original)

    monkeypatch.setattr(ai, "generate_text", fake_generate)
    monkeypatch.setattr(ai, "generate_image", lambda prompt: "img")
    monkeypatch.setattr(ai.file, "generate_worksheet_pdf", lambda payload: b"%PDF")

    model_data, _pdf, _image, _raw, leveling = ai.generate_full_worksheet(
        "9", "9", "science"
    )

    assert leveling["first_draft"]["below_target"] is True
    assert leveling["llm_rewrite_applied"] is True
    assert model_data["story"] == raised
    assert leveling["final"]["in_band"] is True


def test_corrector_path_applies_an_accepted_rewrite(mock_generate, fixture_text):
    mock_generate(_rewrite_json(LEGIT_MILD_REWORD))

    outcome = ai.correct_with_rewrite(
        fixture_text("business_register"), 6, allow_rewrite=True
    )

    assert outcome.gate_passed
    assert outcome.llm_rewrite_applied
    assert outcome.text == LEGIT_MILD_REWORD


# --------------------------------------------------------------------------
# Feature-flagged single-pass LLM mode
# --------------------------------------------------------------------------


def test_default_correction_mode_is_iterative():
    assert rl_config.correction_mode() == rl_config.CORRECTION_MODE_ITERATIVE


def test_iterative_mode_still_runs_operators_a_through_c(
    monkeypatch, mock_generate, fixture_text
):
    monkeypatch.setenv("CORRECTION_MODE", "iterative")
    mock_generate(_rewrite_json(LEGIT_MILD_REWORD))

    outcome = ai.correct_with_rewrite(
        fixture_text("business_register"), 6, allow_rewrite=True
    )

    assert any(
        edit.operator in {"split_sentence", "substitute_word", "simplify_clause"}
        for edit in outcome.correction.edits
    )
    assert outcome.correction.iterations >= 1


def test_single_pass_llm_never_invokes_operators_a_through_c(
    monkeypatch, mock_generate, fixture_text
):
    monkeypatch.setenv("CORRECTION_MODE", "single_pass_llm")
    mock_generate(_rewrite_json(LEGIT_MILD_REWORD))

    outcome = ai.correct_with_rewrite(
        fixture_text("business_register"), 6, allow_rewrite=True
    )

    assert outcome.correction.iterations == 0
    assert outcome.correction.edits == []
    assert outcome.llm_rewrite_applied is True
    assert outcome.text == LEGIT_MILD_REWORD


def test_single_pass_llm_response_has_no_grade_field_and_still_parses():
    raw = json.dumps({
        "passage": LEGIT_MILD_REWORD,
        "grade": 2,
        "estimated_grade": 2.1,
        "reading_level": "grade 2",
    })
    assert ai_rewrite.parse_rewrite_output(raw) == LEGIT_MILD_REWORD
    assert "grade" not in ai_rewrite.REWRITE_OUTPUT_FORMAT.split("{")[1].split("}")[0]


def test_single_pass_reported_grade_comes_from_score_text(
    monkeypatch, mock_generate, fixture_text, table
):
    monkeypatch.setenv("CORRECTION_MODE", "single_pass_llm")
    mock_generate(_rewrite_json(LEGIT_MILD_REWORD))

    outcome = ai.correct_with_rewrite(
        fixture_text("business_register"), 6, allow_rewrite=True
    )

    measured = score_text(LEGIT_MILD_REWORD, table=table)
    assert outcome.final_score.estimated_grade == pytest.approx(
        measured.estimated_grade
    )
    assert outcome.final_score.estimated_grade != pytest.approx(2.0)


def test_single_pass_drift_check_blocks_a_fact_inversion(
    monkeypatch, mock_generate, fixture_text
):
    """Reuse the fact-inversion case from test_verify. Retry once, then keep the original."""
    monkeypatch.setenv("CORRECTION_MODE", "single_pass_llm")
    original = fixture_text("business_register")
    calls = mock_generate(
        _rewrite_json(DRIFT_FACTS_INVERTED),
        _rewrite_json(DRIFT_FACTS_INVERTED),
    )

    outcome = ai.correct_with_rewrite(original, 6, allow_rewrite=True)

    assert len(calls) == 2
    assert outcome.llm_rewrite_applied is False
    assert outcome.failure_reason == "rewrite_rejected_semantic_drift"
    assert outcome.text == original
    assert outcome.drift_report is not None
    assert outcome.drift_report.passed is False


def test_single_pass_does_not_polish_with_operators_a_b(monkeypatch):
    monkeypatch.setenv("CORRECTION_MODE", "single_pass_llm")
    band = rl_bands.target_band(3)
    outcome = ai_rewrite.RewriteOutcome(
        text="still too hard",
        correction=SimpleNamespace(target_band=band, edits=[]),
        final_score=SimpleNamespace(estimated_grade=3.66),
        gate_passed=False,
        failure_reason="rewrite_rejected_out_of_band",
        llm_rewrite_applied=True,
    )

    def fail(*args, **kwargs):
        raise AssertionError("single_pass_llm must not run Operators A/B after the rewrite")

    monkeypatch.setattr(ai, "correct_text", fail)
    assert ai._polish_rewrite_with_repair(outcome, 3, []) is outcome


# --------------------------------------------------------------------------
# Part 4: naturalness is sampled, non-blocking, and off by default
# --------------------------------------------------------------------------


class _AlwaysSample:
    @staticmethod
    def random():
        return 0.0


def test_naturalness_is_off_by_default(monkeypatch):
    def explode(prompt):
        raise AssertionError("naturalness check ran while disabled")

    assert rl_config.ENABLE_NATURALNESS_CHECK is False
    from reading_level import bands

    assert (
        ai_rewrite.check_naturalness(
            "Some text.", bands.target_band(4), generate=explode
        )
        is None
    )


def test_naturalness_logs_when_enabled_and_sampled(monkeypatch):
    from reading_level import bands

    monkeypatch.setattr(rl_config, "ENABLE_NATURALNESS_CHECK", True)
    monkeypatch.setattr(rl_config, "NATURALNESS_SAMPLE_RATE", 1.0)

    report = ai_rewrite.check_naturalness(
        "The dog ran.",
        bands.target_band(4),
        generate=lambda p: '{"score": 4, "rationale": "Reads naturally."}',
        rng=_AlwaysSample,
    )

    assert report == {"score": 4, "rationale": "Reads naturally."}


def test_naturalness_never_raises_on_a_bad_response(monkeypatch):
    from reading_level import bands

    monkeypatch.setattr(rl_config, "ENABLE_NATURALNESS_CHECK", True)
    monkeypatch.setattr(rl_config, "NATURALNESS_SAMPLE_RATE", 1.0)

    assert (
        ai_rewrite.check_naturalness(
            "The dog ran.",
            bands.target_band(4),
            generate=lambda p: "not json at all",
            rng=_AlwaysSample,
        )
        is None
    )


def test_naturalness_respects_the_sample_rate(monkeypatch):
    from reading_level import bands

    monkeypatch.setattr(rl_config, "ENABLE_NATURALNESS_CHECK", True)
    monkeypatch.setattr(rl_config, "NATURALNESS_SAMPLE_RATE", 0.0)

    class _NeverSample:
        @staticmethod
        def random():
            return 0.99

    assert (
        ai_rewrite.check_naturalness(
            "The dog ran.",
            bands.target_band(4),
            generate=lambda p: '{"score": 1}',
            rng=_NeverSample,
        )
        is None
    )


def test_image_prompt_ages_characters_to_classroom_grade():
    ninth = ai.create_image_prompt("The Game", "Jamal ran down the field.", grade="9")
    assert "9th grade" in ninth
    assert "14-15 year olds" in ninth
    assert "children's" not in ninth.lower()

    kinder = ai.create_image_prompt("The Park", "Sam played tag.", grade="K")
    assert "kindergarten" in kinder
    assert "5-6 year olds" in kinder


def test_image_prompt_does_not_ask_flux_to_paint_title_or_story_text():
    prompt = ai.create_image_prompt(
        "The Dinosaur's Big Roar",
        "Tim loved to stomp in the jungle.",
        grade="2",
    )
    lowered = prompt.lower()
    assert "titled" not in lowered
    assert "the dinosaur's big roar" not in lowered
    assert "tim loved to stomp in the jungle" in lowered
    assert "picture only" in lowered
    assert "worksheet title" in lowered
    assert "action-beat" in lowered
