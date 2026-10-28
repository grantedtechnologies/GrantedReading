"""Smoke tests for the simple reading-level analyzers."""

from reading_level import analyze_passage, score_text, vocab_for_level
from reading_level.sentences import split_sentences
from reading_level.word_frequency import find_hard_words, tokenize_words


EASY = (
    "The cat sat on the mat. The dog ran to the park. "
    "They played all day in the sun."
)

HARDER = (
    "The unprecedented proliferation of subterranean infrastructure "
    "necessitated comprehensive geological surveys before excavation commenced. "
    "Engineers deliberated extensively regarding structural contingencies."
)


def test_tokenize_words():
    assert "hello" in tokenize_words("Hello, world!")


def test_split_sentences():
    parts = split_sentences("Hello there. How are you? Fine!")
    assert len(parts) == 3


def test_score_text_returns_grade():
    score = score_text(EASY)
    assert 0 <= score.estimated_grade <= 13
    assert score.confidence in ("high", "low")


def test_harder_passage_scores_higher():
    assert score_text(HARDER).estimated_grade > score_text(EASY).estimated_grade


def test_analyze_passage_shape():
    report = analyze_passage(HARDER, 2)
    assert report["in_band"] is False
    assert "too_hard" in report
    assert "words" in report["too_hard"]
    assert isinstance(report["level_vocab"], list)


def test_vocab_for_level_nonempty():
    words = vocab_for_level(3, limit=20)
    assert len(words) > 0


def test_find_hard_words_on_hard_passage():
    hard = find_hard_words(HARDER, grade=2)
    assert any(item["word"] for item in hard)
