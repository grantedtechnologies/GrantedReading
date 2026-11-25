"""Create a class with multiple students and vocab lists; empty-input cases."""

from __future__ import annotations

import db
import pytest

from tests.e2e.conftest import login_as
from tests.e2e.helpers.ui import clear_testid_text, open_vocab_section, wait_for_testid_text


@pytest.fixture
def teacher_ready(unique_email, tracked_teachers):
    teacher = db.create_teacher(
        "E2E",
        "Classes",
        unique_email,
        "Passw0rd!",
        "Test School",
        "",
        "United States",
        "Ohio",
        "Columbus",
    )
    db.mark_teacher_verified(teacher["teacher_id"])
    tracked_teachers.append(teacher["teacher_id"])
    return teacher


def _create_class_ui(page, base_url: str, name: str = "E2E Reading", case=None) -> None:
    # Stay on setup (no auto-redirect to new-student) so status is readable.
    page.goto(f"{base_url}/setup?next=account")
    page.get_by_test_id("class-name").fill(name)
    page.get_by_test_id("class-subject").fill("English")
    page.locator("#class-grade").select_option(index=1)
    page.locator("#class-year").select_option(index=0)
    page.locator("#class-type").select_option("standard")
    page.get_by_test_id("class-submit").click()
    status = wait_for_testid_text(page, "class-status")
    if case is not None:
        case.expect_contains(
            status,
            "Class added",
            input={"name": name, "subject": "English"},
            label="class created",
        )
    else:
        assert "Class added" in status


def _add_student_with_vocab(
    page,
    base_url: str,
    class_id: int,
    *,
    first: str,
    last: str,
    notes: str,
    list_name: str,
    words: str,
) -> None:
    page.goto(f"{base_url}/classes/{class_id}/students/new")
    page.get_by_test_id("student-first-name").fill(first)
    page.get_by_test_id("student-last-name").fill(last)
    page.get_by_test_id("student-reading-level").select_option(index=2)
    page.get_by_test_id("student-classroom-grade").select_option(index=2)
    page.get_by_test_id("student-notes").fill(notes)
    open_vocab_section(page)
    page.get_by_test_id("add-vocab-btn").click()
    page.get_by_test_id("add-list-form").wait_for(state="visible")
    page.get_by_test_id("vocab-list-name").fill(list_name)
    page.get_by_test_id("vocab-list-words").fill(words)
    page.get_by_test_id("student-save").click()
    page.wait_for_url(f"**/classes/{class_id}", timeout=15000)


def test_empty_class_name_shows_error(page, base_url, teacher_ready, case):
    case.set(input={"name": "", "subject": "English"}, expected="name/subject error")
    login_as(page, base_url, teacher_ready["email"])
    page.goto(f"{base_url}/setup")
    page.get_by_test_id("class-name").fill("")
    page.get_by_test_id("class-subject").fill("English")
    page.get_by_test_id("class-submit").click()
    text = wait_for_testid_text(page, "class-status").lower()
    case.set(actual=text)
    case.expect(
        ("name" in text) or ("subject" in text),
        True,
        input={"status": text},
        label="empty class name error",
    )
    case.expect(db.list_classes_for_teacher(teacher_ready["teacher_id"]), [], label="no class row")


def test_empty_class_subject_shows_error(page, base_url, teacher_ready, case):
    case.set(input={"name": "Empty Subject Class", "subject": ""})
    login_as(page, base_url, teacher_ready["email"])
    page.goto(f"{base_url}/setup")
    page.get_by_test_id("class-name").fill("Empty Subject Class")
    page.get_by_test_id("class-subject").fill("")
    page.get_by_test_id("class-submit").click()
    text = wait_for_testid_text(page, "class-status").lower()
    case.expect(
        ("name" in text) or ("subject" in text),
        True,
        input={"status": text},
        label="empty subject error",
    )
    classes = db.list_classes_for_teacher(teacher_ready["teacher_id"])
    case.expect(
        all(c["name"] != "Empty Subject Class" for c in classes),
        True,
        input={"classes": [c["name"] for c in classes]},
        label="class not created",
    )


def test_empty_student_name_shows_error(page, base_url, teacher_ready, case):
    case.set(input={"first": "", "last": ""}, expected="name error")
    login_as(page, base_url, teacher_ready["email"])
    _create_class_ui(page, base_url, "Empty Student Class", case=case)
    class_id = next(
        c["class_id"]
        for c in db.list_classes_for_teacher(teacher_ready["teacher_id"])
        if c["name"] == "Empty Student Class"
    )
    page.goto(f"{base_url}/classes/{class_id}/students/new")
    page.get_by_test_id("student-first-name").fill("")
    page.get_by_test_id("student-last-name").fill("")
    page.get_by_test_id("student-save").click()
    text = wait_for_testid_text(page, "student-status").lower()
    case.expect_contains(text, "name", label="empty student name")
    case.expect(
        db.list_students_for_class(teacher_ready["teacher_id"], class_id),
        [],
        label="no student row",
    )


def test_empty_vocab_list_shows_errors(page, base_url, teacher_ready, case):
    case.set(input={"list_name": "", "words": ""})
    login_as(page, base_url, teacher_ready["email"])
    classroom = db.create_class(
        teacher_ready["teacher_id"], "Vocab Empty", "English", 4, "2026-2027", "standard"
    )
    student = db.create_student(
        teacher_ready["teacher_id"],
        classroom["class_id"],
        "Nova",
        "Empty",
        4,
        3,
        "likes cats",
    )
    page.goto(
        f"{base_url}/classes/{classroom['class_id']}/students/{student['student_id']}"
    )
    open_vocab_section(page)
    page.get_by_test_id("add-vocab-btn").click()
    page.get_by_test_id("vocab-list-save").click()
    text = wait_for_testid_text(page, "vocab-list-status", contains="name").lower()
    case.expect_contains(
        text,
        "name",
        input={"list_name": "", "words": ""},
        label="vocab missing name",
    )

    page.get_by_test_id("vocab-list-name").fill("Space words")
    page.get_by_test_id("vocab-list-words").fill("")
    clear_testid_text(page, "vocab-list-status")
    page.get_by_test_id("vocab-list-save").click()
    text = wait_for_testid_text(page, "vocab-list-status", contains="word").lower()
    case.expect_contains(
        text,
        "word",
        input={"list_name": "Space words", "words": ""},
        label="vocab missing words",
    )

    refreshed = db.find_student_for_teacher(
        teacher_ready["teacher_id"], student["student_id"]
    )
    case.expect(refreshed["vocab_lists"], [], label="no vocab lists saved")


def test_create_class_with_three_students_and_vocab_lists(page, base_url, teacher_ready, case):
    case.set(input={"class": "E2E Trio", "students": 3})
    login_as(page, base_url, teacher_ready["email"])
    _create_class_ui(page, base_url, "E2E Trio", case=case)
    class_id = next(
        c["class_id"]
        for c in db.list_classes_for_teacher(teacher_ready["teacher_id"])
        if c["name"] == "E2E Trio"
    )

    roster = [
        ("Avery", "Adams", "space", "Space words", "orbit, planet, rocket"),
        ("Blake", "Baker", "soccer", "Soccer words", "goal, pass, teammate"),
        ("Casey", "Cole", "cooking", "Kitchen words", "stir, recipe, oven"),
    ]
    for first, last, notes, list_name, words in roster:
        _add_student_with_vocab(
            page,
            base_url,
            class_id,
            first=first,
            last=last,
            notes=notes,
            list_name=list_name,
            words=words,
        )

    students = db.list_students_for_class(teacher_ready["teacher_id"], class_id)
    case.expect(len(students), 3, input={"students": [s["first_name"] for s in students]})
    by_first = {s["first_name"]: s for s in students}
    for first, _last, _notes, list_name, words in roster:
        student = by_first[first]
        case.expect(len(student["vocab_lists"]), 1, input={"student": first})
        vocab = student["vocab_lists"][0]
        case.expect(vocab["list_name"], list_name, input={"student": first})
        expected_words = [w.strip() for w in words.split(",")]
        case.expect(
            sorted(vocab["words"]),
            sorted(expected_words),
            input={"student": first, "list": list_name},
            label=f"vocab words for {first}",
        )

    avery = by_first["Avery"]
    page.goto(f"{base_url}/classes/{class_id}/students/{avery['student_id']}")
    page.get_by_test_id("student-notes").fill("Interested in dinosaurs")
    page.get_by_test_id("student-save").click()
    page.wait_for_url(f"**/classes/{class_id}", timeout=15000)
    refreshed = db.find_student_for_teacher(
        teacher_ready["teacher_id"], avery["student_id"]
    )
    case.expect_contains(refreshed["notes"] or "", "dinosaurs", label="notes updated")

    blake = by_first["Blake"]
    page.goto(f"{base_url}/classes/{class_id}")
    delete_btn = page.locator(
        f'button.delete-student[data-student-id="{blake["student_id"]}"]'
    )
    page.once("dialog", lambda dialog: dialog.accept())
    # Already on /classes/<id>; wait for the row to vanish after DELETE+reload.
    with page.expect_navigation(timeout=15000):
        delete_btn.click()
    delete_btn.wait_for(state="detached", timeout=15000)
    case.expect(
        page.locator(
            f'button.delete-student[data-student-id="{blake["student_id"]}"]'
        ).count(),
        0,
        label="blake delete button gone",
    )
    remaining = db.list_students_for_class(teacher_ready["teacher_id"], class_id)
    case.expect(len(remaining), 2, label="two students remain")
    case.expect(
        all(s["student_id"] != blake["student_id"] for s in remaining),
        True,
        label="blake removed from DB",
    )


def test_create_class_and_students(page, base_url, teacher_ready, case):
    """Smoke path kept for a single student create/edit/delete."""
    case.set(input={"class": "E2E Reading", "student": "Sam Solo"})
    login_as(page, base_url, teacher_ready["email"])
    _create_class_ui(page, base_url, "E2E Reading", case=case)
    class_id = next(
        c["class_id"]
        for c in db.list_classes_for_teacher(teacher_ready["teacher_id"])
        if c["name"] == "E2E Reading"
    )

    page.goto(f"{base_url}/classes/{class_id}/students/new")
    page.get_by_test_id("student-first-name").fill("Sam")
    page.get_by_test_id("student-last-name").fill("Solo")
    page.get_by_test_id("student-reading-level").select_option(index=2)
    page.get_by_test_id("student-classroom-grade").select_option(index=2)
    page.get_by_test_id("student-notes").fill("Interested in space")
    page.get_by_test_id("student-save").click()
    page.wait_for_url(f"**/classes/{class_id}", timeout=15000)

    students = db.list_students_for_class(teacher_ready["teacher_id"], class_id)
    case.expect(
        any(s["first_name"] == "Sam" for s in students),
        True,
        input={"students": [s["first_name"] for s in students]},
        label="Sam created",
    )
    student = next(s for s in students if s["first_name"] == "Sam")

    page.goto(f"{base_url}/classes/{class_id}/students/{student['student_id']}")
    page.get_by_test_id("student-notes").fill("Interested in dinosaurs")
    page.get_by_test_id("student-save").click()
    page.wait_for_url(f"**/classes/{class_id}", timeout=15000)

    page.goto(f"{base_url}/classes/{class_id}")
    delete_btn = page.locator(
        f'button.delete-student[data-student-id="{student["student_id"]}"]'
    )
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_navigation(timeout=15000):
        delete_btn.click()
    delete_btn.wait_for(state="detached", timeout=15000)
    case.expect(
        page.locator(
            f'button.delete-student[data-student-id="{student["student_id"]}"]'
        ).count(),
        0,
        label="student deleted",
    )
