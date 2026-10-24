"""Gate integration tests.

The language model is mocked throughout: nothing here touches the network.
"""

import json

import pytest

import ai
from reading_level import ReadingLevelError, config as rl_config


def _worksheet_json(story):
    return json.dumps(
        {
            "title": "A Story",
            "story": story,
            "questions": {
                "q1": "Who is in the story?",
                "q2": "What happened?",
                "q3": "When did it happen?",
                "q4": "Where did it happen?",
                "q5": "Why did it happen?",
            },
        }
    )


@pytest.fixture
def mock_llm(monkeypatch):
    """Replace the model call with a canned response and count invocations."""

    def _install(story):
        calls = []

        def fake_generate_text(prompt):
            calls.append(prompt)
            return _worksheet_json(story)

        def fake_generate_json(prompt, schema, *, name="response"):
            calls.append(prompt)
            return json.loads(_worksheet_json(story))

        monkeypatch.setattr(ai, "generate_text", fake_generate_text)
        monkeypatch.setattr(ai, "generate_json", fake_generate_json)
        return calls

    return _install


def test_gate_raises_rather_than_returning_out_of_band_text(
    mock_llm, fixture_text
):
    mock_llm(fixture_text("hard_secondary"))

    with pytest.raises(ReadingLevelError) as excinfo:
        ai.generate_leveled_passage("composting", 2)

    assert excinfo.value.reason is not None
    assert excinfo.value.band.display == "Grade 2"


def test_gate_caps_the_number_of_model_calls(mock_llm, fixture_text):
    calls = mock_llm(fixture_text("hard_secondary"))

    with pytest.raises(ReadingLevelError):
        ai.generate_leveled_passage("composting", 2)

    assert len(calls) == rl_config.MAX_GENERATION_ATTEMPTS


def test_the_second_call_names_the_specific_failure(mock_llm, fixture_text):
    """The second call is Operator D, not a blind regeneration.

    Under a shared call budget a diagnostic rewrite is a better use of the
    second call than regenerating and hoping, so the retry ladder now escalates
    instead of re-prompting. It must still be specific about what was wrong --
    that requirement moved from feedback prose into the diagnosis.
    """
    calls = mock_llm(fixture_text("hard_secondary"))

    with pytest.raises(ReadingLevelError):
        ai.generate_leveled_passage("composting", 2)

    rewrite_prompt = calls[1]
    assert "reading-level editor" in rewrite_prompt
    assert "grade levels too high" in rewrite_prompt
    assert "Grade 2" in rewrite_prompt


def test_in_band_passage_is_returned(mock_llm, fixture_text):
    mock_llm(fixture_text("easy_primary"))

    result = ai.generate_leveled_passage("a dog named Sam", 2)

    assert result["reading_level"]["in_band"] is True
    assert result["reading_level"]["target_band"] == "Grade 2"
    assert result["passage"]


def test_easier_than_target_escalates_then_raises_if_still_out(
    mock_llm, fixture_text
):
    """Below-band drafts get Operator D; a miss is a hard error."""
    calls = mock_llm(fixture_text("easy_primary"))

    with pytest.raises(ReadingLevelError) as excinfo:
        ai.generate_leveled_passage("a dog named Sam", 9)

    assert excinfo.value.reason is not None
    assert len(calls) == rl_config.MAX_GENERATION_ATTEMPTS
    assert "too low" in calls[1]


def test_response_never_exposes_raw_features(mock_llm, fixture_text):
    mock_llm(fixture_text("easy_primary"))

    result = ai.generate_leveled_passage("a dog named Sam", 2)

    assert set(result["reading_level"]) == {
        "estimated_grade",
        "target_band",
        "in_band",
        "confidence",
    }


def test_questions_are_scored_separately(mock_llm, fixture_text):
    mock_llm(fixture_text("easy_primary"))

    result = ai.generate_leveled_passage("a dog named Sam", 2)

    assert set(result["question_levels"]) == {"q1", "q2", "q3", "q4", "q5"}
    # Single stems are far too short to measure reliably.
    assert all(q["confidence"] == "low" for q in result["question_levels"].values())


def test_correction_metadata_is_reported(mock_llm, fixture_text):
    mock_llm(fixture_text("easy_primary"))

    result = ai.generate_leveled_passage(
        "a dog named Sam", 2, protected_terms=["Sam"]
    )

    assert result["corrections_applied"] >= 0
    assert "Sam" in result["protected_terms_preserved"]
