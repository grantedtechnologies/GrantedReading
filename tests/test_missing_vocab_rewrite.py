"""Missing focus-vocab detection drives a second rewrite pass."""

import llm_pipeline
from reading_level import score as rl_score


def test_missing_protected_terms_finds_absent_words():
    text = "Mia ran to the park with her red dog."
    assert llm_pipeline.missing_protected_terms(text, ["resilient", "Mia"]) == ["resilient"]
    assert llm_pipeline.missing_protected_terms(text, ["mia", "park"]) == []
    assert llm_pipeline.missing_protected_terms(text, []) == []


def test_parse_focus_vocabulary_keeps_a_full_unit_list():
    raw = (
        "colony, settle, trade, war, native, culture, policy, philosophy, ancient"
    )
    terms = llm_pipeline.parse_focus_vocabulary(raw)
    assert terms == [
        "colony",
        "settle",
        "trade",
        "war",
        "native",
        "culture",
        "policy",
        "philosophy",
        "ancient",
    ]
    assert len(llm_pipeline.parse_focus_vocabulary(terms)) == 9


def test_brief_includes_missing_vocabulary():
    report = rl_score.analyze_passage(
        "Mia has a red dog. The dog is named Sam.",
        1,
    )
    brief = llm_pipeline._brief_for_prompt(
        report,
        ["resilient"],
        missing_vocabulary=["resilient"],
    )
    assert "resilient" in brief
    assert "missing_vocabulary" in brief
    assert "Focus vocabulary missing" in brief


def test_level_story_rewrites_when_in_band_but_vocab_missing(monkeypatch):
    """In-band drafts still rewrite if focus vocab never made it into the story."""
    passage = "Mia has a red dog. The dog is named Sam."
    real_analyze = rl_score.analyze_passage

    def in_band_analyze(text, target_grade):
        report = real_analyze(text, target_grade)
        report["in_band"] = True
        report["below_target"] = False
        return report

    monkeypatch.setattr(rl_score, "analyze_passage", in_band_analyze)
    monkeypatch.setattr(llm_pipeline.rl_score, "analyze_passage", in_band_analyze)

    calls = []

    def fake_generate(user, system=None):
        calls.append(user)
        if len(calls) == 1:
            # First pass still forgets the word.
            return passage
        return passage + " Mia felt resilient when she helped Sam."

    outcome = llm_pipeline.level_story(
        passage,
        1,
        protected_terms=["resilient"],
        allow_rewrite=True,
        generate=fake_generate,
    )

    assert len(calls) == 2
    assert "resilient" in calls[0]
    assert "resilient" in calls[1]
    assert "resilient" in outcome.text.lower()
    assert outcome.llm_rewrite_applied


def test_level_story_second_pass_only_when_first_still_misses(monkeypatch):
    passage = "Sam the dog ran home with Mia after school."
    calls = []

    def fake_generate(user, system=None):
        calls.append(user)
        return passage + " Mia stayed resilient in the rain."

    outcome = llm_pipeline.level_story(
        passage,
        1,
        protected_terms=["resilient"],
        allow_rewrite=True,
        generate=fake_generate,
    )

    assert len(calls) == 1
    assert "resilient" in outcome.text.lower()
