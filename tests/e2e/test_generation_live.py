"""Live-tier generation (Azure LLM + Runpod). Skipped unless -m live."""

from __future__ import annotations

import os

import db
import pytest
import textstat

from tests.e2e.conftest import login_as
from tests.e2e.helpers import judge

# Tunable band for textstat flesch_kincaid_grade vs requested reading level.
READING_LEVEL_BANDS = {
    # requested_level: (min_grade, max_grade) inclusive-ish
    1: (0.0, 3.5),
    2: (0.5, 4.5),
    3: (1.5, 5.5),
    4: (2.5, 6.5),
    5: (3.5, 7.5),
    6: (4.0, 8.5),
}

DOK_MATCH_THRESHOLD = float(os.getenv("E2E_DOK_MATCH_THRESHOLD") or "0.8")


pytestmark = pytest.mark.live


@pytest.fixture
def live_teacher(unique_email, tracked_teachers):
    if (os.getenv("MOCK_AI") or "").lower() in {"1", "true", "yes", "on"}:
        pytest.skip("Unset MOCK_AI for live generation tests.")
    teacher = db.create_teacher(
        "E2E",
        "Live",
        unique_email,
        "Passw0rd!",
        "Test School",
        "",
        "United States",
        "Ohio",
        "Columbus",
    )
    db.mark_teacher_verified(teacher["teacher_id"])
    classroom = db.create_class(
        teacher["teacher_id"], "E2E Live", "English", 4, "2026-2027", "standard"
    )
    student = db.create_student(
        teacher["teacher_id"],
        classroom["class_id"],
        "Live",
        "Student",
        4,
        4,
        "british history",
    )
    tracked_teachers.append(teacher["teacher_id"])
    return {"teacher": teacher, "class": classroom, "student": student}


def test_live_generation_structure_level_and_judge(page, base_url, live_teacher):
    interest = "british history"
    reading_level = 4
    login_as(page, base_url, live_teacher["teacher"]["email"])
    page.goto(f"{base_url}/worksheet")
    page.get_by_test_id("class-select").select_option(str(live_teacher["class"]["class_id"]))
    page.get_by_test_id("student-select").select_option(
        str(live_teacher["student"]["student_id"])
    )
    page.get_by_test_id("interests").fill(interest)
    page.get_by_test_id("generate-btn").click()
    page.wait_for_function(
        """() => {
          const frame = document.querySelector('[data-testid="worksheet-preview"]');
          return frame && frame.getAttribute('src');
        }""",
        timeout=300000,
    )

    sheets = db.list_worksheets_for_teacher(live_teacher["teacher"]["teacher_id"])
    assert sheets
    # Prefer content from the latest version if available via API/history facts.
    # Fall back to generating through pipeline helpers is not needed if content JSON exists.
    version = sheets[0]
    content = version.get("content") or {}
    if isinstance(content, str):
        import json

        content = json.loads(content)
    passage = (content.get("story") or content.get("passage") or "").strip()
    questions = content.get("questions") or {}
    assert passage
    assert len(questions) >= 1

    fk = textstat.flesch_kincaid_grade(passage)
    low, high = READING_LEVEL_BANDS[reading_level]
    assert low <= fk <= high, f"FK grade {fk} outside {low}-{high}"

    q_list = [questions[k] for k in sorted(questions.keys())]
    levels = judge.classify_dok(q_list)
    # Default generator DOK is 1 unless the UI requests otherwise.
    assert judge.dok_match_rate(levels, 1) >= DOK_MATCH_THRESHOLD
    assert judge.passage_about_interest(passage, interest) is True
