"""Query changes keep the same results and stay inside the teacher's rows."""
import uuid

import pytest
from sqlalchemy import text

import db


def _skip_without_local_db():
    try:
        url = db._database_url()
    except Exception as exc:
        pytest.skip(f"MySQL is not available: {exc}")
    if "rlwy.net" in url or "railway.internal" in url:
        pytest.skip("refusing to run these tests against the production database")
    try:
        with db.engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception as exc:
        pytest.skip(f"MySQL is not available: {exc}")


def _seed_teachers():
    maya = db.find_teacher_by_email("maya.chen@granted.local")
    luis = db.find_teacher_by_email("luis.rivera@granted.local")
    if maya is None or luis is None:
        pytest.skip("seed_dev.sql is not loaded")
    return maya, luis


def _base_record(**overrides):
    record = {
        "original": True,
        "sheettype": "worksheet",
        "title": "Cats",
        "expectedLevel": 2,
        "trueLevel": 2,
        "targetDOKLevel": "1",
        "informational": False,
        "interest": "cats",
        "source": "initial",
        "content": "{}",
    }
    record.update(overrides)
    return record


def _delete_sheets(sheet_ids):
    with db.engine.begin() as conn:
        for sheet_id in sheet_ids:
            conn.execute(
                text(
                    """
                    DELETE d FROM downloads d
                    JOIN versions v ON v.versionid = d.versionid
                    WHERE v.sheetid = :sheet_id
                    """
                ),
                {"sheet_id": sheet_id},
            )
            conn.execute(
                text("UPDATE versions SET parentVersionid = NULL WHERE sheetid = :sheet_id"),
                {"sheet_id": sheet_id},
            )
            conn.execute(text("DELETE FROM versions WHERE sheetid = :sheet_id"), {"sheet_id": sheet_id})
            conn.execute(text("DELETE FROM worksheets WHERE sheetid = :sheet_id"), {"sheet_id": sheet_id})


def test_downloaded_versions_match_the_previous_counts_and_order():
    _skip_without_local_db()
    maya, luis = _seed_teachers()
    sheet_ids = []
    try:
        first = db.insert_worksheet(maya["teacher_id"], uuid.uuid4().hex, _base_record(title="First"))
        second = db.insert_worksheet(maya["teacher_id"], uuid.uuid4().hex, _base_record(title="Second"))
        undownloaded = db.insert_worksheet(
            maya["teacher_id"], uuid.uuid4().hex, _base_record(title="Undownloaded")
        )
        other = db.insert_worksheet(luis["teacher_id"], uuid.uuid4().hex, _base_record(title="Other"))
        sheet_ids = [
            first["sheetid"],
            second["sheetid"],
            undownloaded["sheetid"],
            other["sheetid"],
        ]
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO downloads (versionid, downloadedAt, format) VALUES
                      (:first, '2026-10-01 10:00:00', 'pdf'),
                      (:first_again, '2026-10-03 10:00:00', 'pdf'),
                      (:second, '2026-10-02 10:00:00', 'pdf'),
                      (:other, '2026-10-04 10:00:00', 'pdf')
                    """
                ),
                {
                    "first": first["versionid"],
                    "first_again": first["versionid"],
                    "second": second["versionid"],
                    "other": other["versionid"],
                },
            )
            previous = conn.execute(
                text(
                    f"""
                    SELECT v.versionid, counts.download_count, counts.last_downloaded
                    FROM (
                      SELECT versionid,
                             COUNT(*) AS download_count,
                             MAX(downloadedAt) AS last_downloaded
                      FROM downloads
                      GROUP BY versionid
                    ) counts
                    JOIN versions v ON v.versionid = counts.versionid
                    JOIN worksheets w ON w.sheetid = v.sheetid
                    WHERE w.teacherid = :teacher_id
                    ORDER BY counts.last_downloaded DESC, v.versionid DESC
                    """
                ),
                {"teacher_id": maya["teacher_id"]},
            ).mappings().all()
        current = db.list_downloaded_versions(maya["teacher_id"])
        assert [item["versionid"] for item in current] == [row["versionid"] for row in previous]
        assert [item["download_count"] for item in current] == [int(row["download_count"]) for row in previous]
        assert [item["last_downloaded"] for item in current] == [row["last_downloaded"] for row in previous]
        assert current[0]["versionid"] == first["versionid"]
        assert current[0]["download_count"] == 2
        assert current[1]["versionid"] == second["versionid"]
        assert undownloaded["versionid"] not in {item["versionid"] for item in current}
        assert other["versionid"] not in {item["versionid"] for item in current}
        limited = db.list_downloaded_versions(maya["teacher_id"], limit=1)
        assert [item["versionid"] for item in limited] == [first["versionid"]]
    finally:
        _delete_sheets(sheet_ids)


def test_student_lookups_stay_inside_the_teacher():
    _skip_without_local_db()
    maya, luis = _seed_teachers()
    maya_students = db.list_students_for_teacher(maya["teacher_id"])
    luis_students = db.list_students_for_teacher(luis["teacher_id"])
    assert maya_students and luis_students
    luis_student = luis_students[0]
    maya_student = maya_students[0]
    assert db.find_student_for_teacher(maya["teacher_id"], luis_student["student_id"]) is None
    assert db.find_student_for_teacher(maya["teacher_id"], maya_student["student_id"])["student_id"] == maya_student["student_id"]
    assert db.list_students_for_class(maya["teacher_id"], luis_student["class_id"]) == []
    classmates = db.list_students_for_class(maya["teacher_id"], maya_student["class_id"])
    assert {student["student_id"] for student in classmates} == {
        student["student_id"]
        for student in maya_students
        if student["class_id"] == maya_student["class_id"]
    }
    assert db.find_class_for_teacher(maya["teacher_id"], luis_student["class_id"]) is None
    classroom = db.find_class_for_teacher(maya["teacher_id"], maya_student["class_id"])
    listed = next(
        item for item in db.list_classes_for_teacher(maya["teacher_id"])
        if item["class_id"] == maya_student["class_id"]
    )
    assert classroom == listed


def _ensure_answer_unique_index():
    with db.engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'Answer'
                  AND INDEX_NAME = 'answer_teacherid_questionid_unique'
                """
            )
        ).scalar()
        if int(exists or 0):
            return
        duplicates = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM (
                  SELECT 1
                  FROM Answer
                  GROUP BY teacherid, questionid
                  HAVING COUNT(*) > 1
                ) dups
                """
            )
        ).scalar()
        if int(duplicates or 0):
            pytest.fail("local Answer has duplicate teacher/question rows; unique index was not added")
        conn.execute(
            text(
                """
                ALTER TABLE Answer
                ADD UNIQUE INDEX answer_teacherid_questionid_unique (teacherid, questionid)
                """
            )
        )


def test_resubmitting_a_survey_updates_answers_without_duplicates():
    _skip_without_local_db()
    maya, _luis = _seed_teachers()
    _ensure_answer_unique_index()
    survey_id = None
    question_ids = []
    try:
        with db.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO Survey (surveyDate, title, description, baseline, pilotid)
                    VALUES ('2026-11-06 00:00:00', 'Query opt survey', 'Check upsert', 0, NULL)
                    """
                )
            )
            survey_id = result.lastrowid
            for content in ("First question?", "Second question?"):
                inserted = conn.execute(
                    text(
                        """
                        INSERT INTO Question (content, surveyid, questionType)
                        VALUES (:content, :survey_id, 'text')
                        """
                    ),
                    {"content": content, "survey_id": survey_id},
                )
                question_ids.append(inserted.lastrowid)
        db.save_survey_answers(
            maya["teacher_id"],
            survey_id,
            {question_ids[0]: "one", question_ids[1]: "two"},
        )
        db.save_survey_answers(
            maya["teacher_id"],
            survey_id,
            {question_ids[0]: "updated", question_ids[1]: "still two"},
        )
        with db.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT questionid, content
                    FROM Answer
                    WHERE teacherid = :teacher_id AND questionid IN (:first, :second)
                    ORDER BY questionid
                    """
                ),
                {
                    "teacher_id": maya["teacher_id"],
                    "first": question_ids[0],
                    "second": question_ids[1],
                },
            ).mappings().all()
        assert [(row["questionid"], row["content"]) for row in rows] == [
            (question_ids[0], "updated"),
            (question_ids[1], "still two"),
        ]
        listed = next(
            survey for survey in db.list_surveys_for_teacher(maya["teacher_id"])
            if survey["survey_id"] == survey_id
        )
        with db.engine.connect() as conn:
            previous = db._questions_with_answers(conn, maya["teacher_id"], survey_id)
        assert listed["questions"] == previous
    finally:
        if survey_id is not None:
            id_list = ",".join(str(int(qid)) for qid in question_ids) or "0"
            with db.engine.begin() as conn:
                conn.execute(text(f"DELETE FROM Answer WHERE questionid IN ({id_list})"))
                conn.execute(text(f"DELETE FROM Question WHERE questionid IN ({id_list})"))
                conn.execute(
                    text("DELETE FROM Survey WHERE surveyid = :survey_id"),
                    {"survey_id": survey_id},
                )


def test_insert_words_batches_empty_and_multiple_lists():
    _skip_without_local_db()
    maya, _luis = _seed_teachers()
    students = db.list_students_for_teacher(maya["teacher_id"])
    if not students:
        pytest.skip("seed_dev.sql has no students")
    created_ids = []
    try:
        empty = db.create_vocab_list(maya["teacher_id"], students[0]["student_id"], "Empty", "", [])
        many = db.create_vocab_list(
            maya["teacher_id"], students[0]["student_id"], "Many", "", ["alpha", "beta", "gamma"]
        )
        created_ids = [empty["vocab_list_id"], many["vocab_list_id"]]
        assert empty["words"] == []
        assert many["words"] == ["alpha", "beta", "gamma"]
        with db.engine.connect() as conn:
            empty_count = conn.execute(
                text("SELECT COUNT(*) FROM words WHERE vocabListid = :id"),
                {"id": empty["vocab_list_id"]},
            ).scalar()
            words = conn.execute(
                text("SELECT word FROM words WHERE vocabListid = :id ORDER BY wordid"),
                {"id": many["vocab_list_id"]},
            ).scalars().all()
        assert int(empty_count) == 0
        assert list(words) == ["alpha", "beta", "gamma"]
    finally:
        with db.engine.begin() as conn:
            for vocab_list_id in created_ids:
                conn.execute(
                    text("DELETE FROM words WHERE vocabListid = :id"),
                    {"id": vocab_list_id},
                )
                conn.execute(
                    text("DELETE FROM vocabList WHERE vocabListid = :id"),
                    {"id": vocab_list_id},
                )
