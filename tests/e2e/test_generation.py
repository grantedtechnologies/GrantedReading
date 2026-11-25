"""Fast-tier generation with MOCK_AI=true: full generate, text/image updates, empty inputs."""

from __future__ import annotations

import db
import pytest

from tests.e2e.conftest import login_as
from tests.e2e.helpers import db as e2e_db
from tests.e2e.helpers import runpod_helpers


@pytest.fixture
def gen_teacher(unique_email, tracked_teachers):
    teacher = db.create_teacher(
        "E2E",
        "Gen",
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
        teacher["teacher_id"], "E2E Gen", "English", 4, "2026-2027", "standard"
    )
    student = db.create_student(
        teacher["teacher_id"],
        classroom["class_id"],
        "Sam",
        "Reader",
        4,
        3,
        "loves dinosaurs",
    )
    tracked_teachers.append(teacher["teacher_id"])
    return {"teacher": teacher, "class": classroom, "student": student}


def _wait_for_preview(page, timeout: int = 60000) -> None:
    preview = page.get_by_test_id("worksheet-preview")
    preview.wait_for(state="visible", timeout=timeout)
    page.wait_for_function(
        """() => {
          const frame = document.querySelector('[data-testid="worksheet-preview"]');
          return frame && frame.getAttribute('src');
        }""",
        timeout=timeout,
    )


def _generate_worksheet(page, base_url: str, gen_teacher: dict) -> None:
    page.goto(f"{base_url}/worksheet")
    page.get_by_test_id("class-select").select_option(str(gen_teacher["class"]["class_id"]))
    page.get_by_test_id("student-select").select_option(
        str(gen_teacher["student"]["student_id"])
    )
    page.get_by_test_id("interests").fill("loves dinosaurs")
    page.get_by_test_id("generate-btn").click()
    _wait_for_preview(page)


def test_generate_empty_grade_shows_error(page, base_url, gen_teacher, case):
    case.set(input={"grade": None, "interests": "dinosaurs"}, expected="grade")
    login_as(page, base_url, gen_teacher["teacher"]["email"])
    page.goto(f"{base_url}/worksheet")
    page.get_by_test_id("interests").fill("dinosaurs")
    page.get_by_test_id("generate-btn").click()
    error = page.get_by_test_id("generate-error")
    error.wait_for(state="visible")
    case.expect_contains(error.inner_text().lower(), "grade", label="missing grade")


def test_generate_empty_reading_level_shows_error(page, base_url, gen_teacher, case):
    case.set(input={"grade": "4", "reading_level": None}, expected="reading level")
    login_as(page, base_url, gen_teacher["teacher"]["email"])
    page.goto(f"{base_url}/worksheet")
    page.get_by_test_id("student-grade").select_option("4")
    page.get_by_test_id("interests").fill("dinosaurs")
    page.get_by_test_id("generate-btn").click()
    error = page.get_by_test_id("generate-error")
    error.wait_for(state="visible")
    case.expect_contains(error.inner_text().lower(), "reading level", label="missing level")


def test_generate_empty_interests_shows_error(page, base_url, gen_teacher, case):
    case.set(input={"interests": ""}, expected="interest")
    login_as(page, base_url, gen_teacher["teacher"]["email"])
    page.goto(f"{base_url}/worksheet")
    page.get_by_test_id("class-select").select_option(str(gen_teacher["class"]["class_id"]))
    page.get_by_test_id("student-select").select_option(
        str(gen_teacher["student"]["student_id"])
    )
    page.get_by_test_id("interests").fill("")
    page.get_by_test_id("generate-btn").click()
    error = page.get_by_test_id("generate-error")
    error.wait_for(state="visible")
    case.expect_contains(error.inner_text().lower(), "interest", label="missing interests")


def test_generate_worksheet_shows_preview_and_history(page, base_url, gen_teacher, case):
    teacher = gen_teacher["teacher"]
    case.set(input={"interests": "loves dinosaurs", "student": "Sam Reader"})
    login_as(page, base_url, teacher["email"])
    _generate_worksheet(page, base_url, gen_teacher)
    case.note("preview visible with src")

    download = page.get_by_test_id("download-btn")
    download.wait_for(state="visible", timeout=10000)
    with page.expect_download(timeout=30000):
        download.click()

    sheets = db.list_worksheets_for_teacher(teacher["teacher_id"])
    case.expect(bool(sheets), True, input={"sheet_count": len(sheets)}, label="worksheet row")

    page.goto(f"{base_url}/worksheets/history")
    page.get_by_test_id("worksheet-history").wait_for(state="visible")
    page.get_by_test_id(f"history-sheet-{sheets[0]['sheetid']}").wait_for(state="visible")
    case.step("history row visible", actual=sheets[0]["sheetid"])


def test_generate_then_update_text_and_image(page, base_url, gen_teacher, case):
    case.set(input={"interests": "loves dinosaurs"})
    login_as(page, base_url, gen_teacher["teacher"]["email"])
    _generate_worksheet(page, base_url, gen_teacher)

    page.get_by_test_id("edit-worksheet-btn").click()
    page.get_by_test_id("review-form").wait_for(state="visible")

    original_title = page.get_by_test_id("review-title").input_value()
    revised = f"{original_title} — revised"
    page.get_by_test_id("review-title").fill(revised)
    page.get_by_test_id("review-story").fill(
        "Maya liked dinosaurs. She read about fossils with her class and drew a T-rex."
    )

    page.get_by_test_id("update-pdf-btn").click()
    _wait_for_preview(page)
    # After a successful text update the review form stays open, so Edit is hidden.
    page.get_by_test_id("review-form").wait_for(state="visible")
    case.expect_contains(
        page.get_by_test_id("review-title").input_value().lower(),
        "revised",
        input={"title": revised},
        label="text update kept revised title",
    )

    page.get_by_test_id("image-feedback").fill("")
    page.get_by_test_id("regenerate-image-btn").click()
    error = page.get_by_test_id("generate-error")
    error.wait_for(state="visible")
    case.expect_contains(
        error.inner_text().lower(),
        "illustration feedback",
        input={"image_feedback": ""},
        label="regen without feedback",
    )

    page.get_by_test_id("image-feedback").fill("Make the dinosaur brighter and friendlier")
    page.get_by_test_id("regenerate-image-btn").click()
    _wait_for_preview(page)
    case.note("image regen completed; preview still has src")


def test_update_pdf_empty_title_story_shows_error(page, base_url, gen_teacher, case):
    case.set(input={"title": "", "story": ""}, expected="title and story")
    login_as(page, base_url, gen_teacher["teacher"]["email"])
    _generate_worksheet(page, base_url, gen_teacher)
    page.get_by_test_id("edit-worksheet-btn").click()
    page.get_by_test_id("review-title").fill("")
    page.get_by_test_id("review-story").fill("")
    page.get_by_test_id("update-pdf-btn").click()
    error = page.get_by_test_id("generate-error")
    error.wait_for(state="visible")
    text = error.inner_text().lower()
    case.expect_contains(text, "title", label="mentions title")
    case.expect_contains(text, "story", label="mentions story")


def test_runpod_webhook_idempotent(base_url, case):
    job_id = "mock-e2e-idempotent"
    case.set(input={"job_id": job_id})
    e2e_db.delete_runpod_events_like("runpod:mock-e2e-idempotent")
    first = runpod_helpers.post_completed(base_url, job_id=job_id)
    second = runpod_helpers.post_completed(base_url, job_id=job_id)
    case.expect(first.status_code, 200, input={"body": first.text[:200]}, label="first webhook")
    case.expect(second.status_code, 200, input={"body": second.text[:200]}, label="second webhook")
    case.expect(first.json().get("duplicate"), False, label="first not duplicate")
    case.expect(second.json().get("duplicate"), True, label="second is duplicate")
    case.expect(e2e_db.count_runpod_events(job_id), 1, label="one stored event")


def test_whole_class_generation_placeholder():
    pytest.skip("Whole-class generation is not implemented yet.")
