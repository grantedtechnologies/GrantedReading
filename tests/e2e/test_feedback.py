"""Worksheet feedback through the history UI (column layout)."""

from __future__ import annotations

import json
import uuid

import db
import pytest

from tests.e2e.conftest import login_as


@pytest.fixture
def feedback_ready(unique_email, tracked_teachers):
    """Teacher with one downloaded worksheet version ready for feedback."""
    teacher = db.create_teacher(
        "E2E",
        "Feed",
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
        teacher["teacher_id"], "E2E Feed", "English", 4, "2026-2027", "standard"
    )
    student = db.create_student(
        teacher["teacher_id"],
        classroom["class_id"],
        "Feed",
        "Back",
        4,
        3,
        "soccer",
    )
    generation_id = uuid.uuid4().hex
    model_data = {
        "title": "E2E Feedback Worksheet",
        "story": "Maya liked soccer. She practiced with friends after school.",
        "questions": {"q1": "What did Maya like?", "q2": "When did she practice?"},
    }
    record = {
        "original": False,
        "sheettype": "worksheet",
        "title": model_data["title"][:255],
        "expectedLevel": 3,
        "trueLevel": 3,
        "targetDOKLevel": "1",
        "dokMix": {"1": 2},
        "phonics": None,
        "informational": False,
        "interest": "soccer",
        "studentid": student["student_id"],
        "vocabListid": None,
        "sourceurl": None,
        "source": "initial",
        "content": json.dumps(model_data),
    }
    db.insert_worksheet(teacher["teacher_id"], generation_id, record)
    version = db.find_worksheet_by_generation(teacher["teacher_id"], generation_id)
    assert version is not None
    db.insert_download(version["versionid"], "pdf")
    tracked_teachers.append(teacher["teacher_id"])
    return {
        "teacher": teacher,
        "sheet_id": version["sheetid"],
        "generation_id": generation_id,
    }


def _open_history_column(page, base_url: str, sheet_id: int) -> None:
    page.goto(f"{base_url}/worksheets/history")
    page.get_by_test_id("worksheet-history").wait_for(state="visible")
    # Feedback form lives in column layout; grid hides .column-only.
    page.get_by_test_id("history-layout-column").click()
    page.get_by_test_id(f"history-sheet-{sheet_id}").wait_for(state="visible")
    page.get_by_test_id(f"feedback-form-{sheet_id}").wait_for(state="visible")


def test_leave_feedback_on_history(page, base_url, feedback_ready, case):
    teacher = feedback_ready["teacher"]
    sheet_id = feedback_ready["sheet_id"]
    case.set(
        input={"sheet_id": sheet_id, "rating": 5, "feedback": "Worked well with the student."},
        expected="Saved. + DB rating 5",
    )
    login_as(page, base_url, teacher["email"])
    _open_history_column(page, base_url, sheet_id)

    page.get_by_test_id(f"feedback-rating-{sheet_id}").select_option("5")
    page.get_by_test_id(f"feedback-text-{sheet_id}").fill(
        "Worked well with the student."
    )
    page.get_by_test_id(f"feedback-save-{sheet_id}").click()

    status = page.get_by_test_id(f"feedback-status-{sheet_id}")
    status.wait_for(state="visible")
    page.wait_for_function(
        f"""() => {{
          const el = document.querySelector('[data-testid="feedback-status-{sheet_id}"]');
          return el && /saved/i.test((el.textContent || '').trim());
        }}""",
        timeout=15000,
    )
    case.expect_contains(status.inner_text(), "Saved", label="UI status")

    sheets = db.list_worksheets_for_teacher(teacher["teacher_id"])
    match = next(s for s in sheets if int(s["sheetid"]) == int(sheet_id))
    case.expect(
        match.get("rating") == 5 or str(match.get("rating")) == "5",
        True,
        input={"db_rating": match.get("rating")},
        label="DB rating 5",
    )
    case.expect_contains(match.get("feedback") or "", "Worked well", label="DB feedback text")


def test_update_feedback_on_history(page, base_url, feedback_ready, case):
    teacher = feedback_ready["teacher"]
    sheet_id = feedback_ready["sheet_id"]
    case.set(input={"sheet_id": sheet_id, "first": "3/First note.", "then": "4/Updated"})
    login_as(page, base_url, teacher["email"])
    _open_history_column(page, base_url, sheet_id)

    page.get_by_test_id(f"feedback-rating-{sheet_id}").select_option("3")
    page.get_by_test_id(f"feedback-text-{sheet_id}").fill("First note.")
    page.get_by_test_id(f"feedback-save-{sheet_id}").click()
    page.wait_for_function(
        f"""() => {{
          const el = document.querySelector('[data-testid="feedback-status-{sheet_id}"]');
          return el && /saved/i.test((el.textContent || '').trim());
        }}""",
        timeout=15000,
    )

    page.get_by_test_id(f"feedback-rating-{sheet_id}").select_option("4")
    page.get_by_test_id(f"feedback-text-{sheet_id}").fill("Updated after class.")
    page.get_by_test_id(f"feedback-save-{sheet_id}").click()
    page.wait_for_function(
        f"""() => {{
          const el = document.querySelector('[data-testid="feedback-status-{sheet_id}"]');
          return el && /saved/i.test((el.textContent || '').trim());
        }}""",
        timeout=15000,
    )

    sheets = db.list_worksheets_for_teacher(teacher["teacher_id"])
    match = next(s for s in sheets if int(s["sheetid"]) == int(sheet_id))
    case.expect(
        match.get("rating") == 4 or str(match.get("rating")) == "4",
        True,
        input={"db_rating": match.get("rating")},
        label="updated rating 4",
    )
    case.expect_contains(
        match.get("feedback") or "",
        "Updated after class",
        label="updated feedback text",
    )


def test_feedback_rating_only_saves(page, base_url, feedback_ready, case):
    """Empty text with a rating is allowed."""
    teacher = feedback_ready["teacher"]
    sheet_id = feedback_ready["sheet_id"]
    case.set(input={"sheet_id": sheet_id, "rating": 2, "feedback": ""})
    login_as(page, base_url, teacher["email"])
    _open_history_column(page, base_url, sheet_id)

    page.get_by_test_id(f"feedback-rating-{sheet_id}").select_option("2")
    page.get_by_test_id(f"feedback-text-{sheet_id}").fill("")
    page.get_by_test_id(f"feedback-save-{sheet_id}").click()
    page.wait_for_function(
        f"""() => {{
          const el = document.querySelector('[data-testid="feedback-status-{sheet_id}"]');
          return el && /saved/i.test((el.textContent || '').trim());
        }}""",
        timeout=15000,
    )

    sheets = db.list_worksheets_for_teacher(teacher["teacher_id"])
    match = next(s for s in sheets if int(s["sheetid"]) == int(sheet_id))
    case.expect(
        match.get("rating") == 2 or str(match.get("rating")) == "2",
        True,
        input={"db_rating": match.get("rating"), "db_feedback": match.get("feedback")},
        label="rating-only save",
    )
