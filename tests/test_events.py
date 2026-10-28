"""Events table, the log helper, and one row per teacher action."""
import json
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

import app as app_module
import db
import events


def _skip_without_db():
    try:
        with db.engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception as exc:
        pytest.skip(f"MySQL is not available: {exc}")


@pytest.fixture(scope="module")
def mysql():
    _skip_without_db()
    return True


def _scalar(sql, params=None):
    with db.engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar()


def _rows_after(event_id):
    with db.engine.connect() as conn:
        return conn.execute(
            text("SELECT * FROM Events WHERE eventid > :event_id ORDER BY eventid"),
            {"event_id": event_id},
        ).mappings().all()


def _max_event_id():
    return _scalar("SELECT COALESCE(MAX(eventid), 0) FROM Events")


def _expect_one(before, **expected):
    rows = _rows_after(before)
    assert len(rows) == 1
    row = rows[0]
    for key, value in expected.items():
        actual = row[key]
        if key == "success":
            actual = int(actual)
            value = int(value)
        assert actual == value, f"{key}: {row[key]!r} != {value!r}"
    return row


def test_seed_sessions_and_events_match_dev_data(mysql):
    sessions = _scalar("SELECT COUNT(*) FROM sessions")
    event_count = _scalar("SELECT COUNT(*) FROM Events")
    if sessions != 10 or event_count != 36:
        pytest.skip("seed_dev.sql events are not the current database")
    unmatched = _scalar(
        """
        SELECT COUNT(*)
        FROM Events e
        WHERE e.eventType = 'login'
          AND e.success = 1
          AND NOT EXISTS (
            SELECT 1 FROM sessions s
            WHERE s.teacherid = e.teacherid AND s.startedAt = e.eventTime
          )
        """
    )
    assert unmatched == 0
    generated = _scalar(
        "SELECT COUNT(*) FROM Events WHERE eventType = 'worksheet_generated' AND success = 1"
    )
    downloaded = _scalar(
        "SELECT COUNT(*) FROM Events WHERE eventType = 'worksheet_downloaded' AND success = 1"
    )
    assert generated == _scalar("SELECT COUNT(*) FROM worksheets")
    assert downloaded == _scalar("SELECT COUNT(*) FROM downloads")
    assert generated == 0
    assert downloaded == 0


def test_event_checks_reject_bad_rows(mysql):
    with pytest.raises((IntegrityError, OperationalError)):
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO Events (eventType, success, statusCode)
                    VALUES ('login', 0, 500)
                    """
                )
            )
    with pytest.raises((IntegrityError, OperationalError)):
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO Events (eventType, entity, success)
                    VALUES ('class_created', 'class', 1)
                    """
                )
            )


def test_log_event_never_raises_and_drops_secrets(mysql, monkeypatch):
    events.log_event(
        events.LOGIN,
        success=False,
        status_code=401,
        error_message="maya.chen@granted.local",
    )
    stored = _scalar(
        """
        SELECT errorMessage FROM Events
        WHERE eventType = 'login' AND errorMessage = 'unknown_error'
        ORDER BY eventid DESC LIMIT 1
        """
    )
    assert stored == "unknown_error"
    leaked = _scalar(
        """
        SELECT COUNT(*) FROM Events
        WHERE CONCAT_WS(' ', eventType, entity, errorMessage) LIKE '%@%'
           OR CONCAT_WS(' ', eventType, entity, errorMessage) LIKE '%password123%'
        """
    )
    assert leaked == 0

    def broken():
        raise RuntimeError("events table missing")

    monkeypatch.setattr(db.engine, "begin", broken)
    events.log_event(events.LOGOUT, teacher_id=1, success=True, status_code=200)


def test_each_action_writes_one_event(mysql, monkeypatch):
    maya = db.find_teacher_by_email("maya.chen@granted.local")
    if maya is None:
        pytest.skip("seed_dev.sql is not loaded")
    teacher_id = maya["teacher_id"]

    monkeypatch.setattr(app_module.cache, "worksheet_quota_used", lambda teacher_id: False)
    monkeypatch.setattr(app_module.cache, "mark_worksheet_used", lambda teacher_id: None)
    monkeypatch.setattr(
        app_module.cache,
        "stash_pending_worksheet",
        lambda teacher_id, pdf_bytes, record, generation_id=None: generation_id,
    )
    monkeypatch.setattr(app_module.cache, "load_pending_worksheet", lambda generation_id: None)
    monkeypatch.setattr(app_module.blobStorage, "upload_pdf", lambda key, data: None)
    monkeypatch.setattr(
        app_module.blobStorage,
        "download_pdf",
        lambda key: (_ for _ in ()).throw(FileNotFoundError(key)),
    )

    def fake_generate(*_args, **_kwargs):
        return (
            {
                "title": "Event Probe",
                "story": "A dog ran home.",
                "questions": {"q1": "Who ran?"},
                "dok_level": 1,
            },
            b"%PDF-1.4 event",
            "img",
            None,
            {"final": {"estimated_grade": 2}},
            False,
            None,
        )

    monkeypatch.setattr(app_module.llm_pipeline, "generate_full_worksheet", fake_generate)

    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()

    before = _max_event_id()
    unknown = client.post("/login", json={"email": "nobody@granted.local", "password": "nope"})
    assert unknown.status_code == 401
    _expect_one(
        before,
        eventType="login",
        teacherid=None,
        entity=None,
        entityid=None,
        statusCode=401,
        errorMessage="unknown_email",
        success=0,
    )
    assert "nobody@granted.local" not in json.dumps(
        [dict(row) for row in _rows_after(before)], default=str
    )

    before = _max_event_id()
    wrong = client.post(
        "/login",
        json={"email": "maya.chen@granted.local", "password": "not-the-password"},
    )
    assert wrong.status_code == 401
    assert wrong.get_json()["error"] == unknown.get_json()["error"]
    _expect_one(
        before,
        eventType="login",
        teacherid=teacher_id,
        statusCode=401,
        errorMessage="invalid_password",
        success=0,
    )

    before = _max_event_id()
    ok = client.post(
        "/login",
        json={"email": "maya.chen@granted.local", "password": "password123"},
    )
    assert ok.status_code == 200
    _expect_one(before, eventType="login", teacherid=teacher_id, statusCode=200, success=1)

    before = _max_event_id()
    created = client.post("/api/classes", json={
        "name": "Event Period",
        "subject": "English",
        "class_type": "standard",
        "grade_level": 4,
        "school_year": "2026-2027",
    })
    assert created.status_code == 200
    class_id = created.get_json()["class"]["class_id"]
    _expect_one(
        before,
        eventType="class_created",
        teacherid=teacher_id,
        entity="class",
        entityid=class_id,
        statusCode=200,
        success=1,
    )

    before = _max_event_id()
    student = client.post("/api/students", json={
        "class_id": class_id,
        "first_name": "Ada",
        "last_name": "Probe",
        "classroom_grade": 4,
        "reading_level": 2,
    })
    assert student.status_code == 200
    student_id = student.get_json()["student"]["student_id"]
    _expect_one(
        before,
        eventType="student_created",
        teacherid=teacher_id,
        entity="student",
        entityid=student_id,
        statusCode=200,
        success=1,
    )

    before = _max_event_id()
    vocab = client.post("/api/vocab-lists", json={
        "student_id": student_id,
        "list_name": "Probe words",
        "description": "for the event test",
        "words": ["cat", "hat"],
    })
    assert vocab.status_code == 200
    vocab_id = vocab.get_json()["vocab_list"]["vocab_list_id"]
    _expect_one(
        before,
        eventType="vocab_list_created",
        teacherid=teacher_id,
        entity="vocabList",
        entityid=vocab_id,
        statusCode=200,
        success=1,
    )

    before = _max_event_id()
    renamed = client.put(f"/api/vocab-lists/{vocab_id}", json={
        "list_name": "Probe words renamed",
        "description": "for the event test",
        "words": ["hat", "map"],
    })
    assert renamed.status_code == 200
    _expect_one(
        before,
        eventType="vocab_list_updated",
        teacherid=teacher_id,
        entity="vocabList",
        entityid=vocab_id,
        statusCode=200,
        success=1,
    )

    before = _max_event_id()
    with db.engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO worksheets (
                  teacherid, studentid, vocabListid, title, sheettype, original,
                  informational, interest, expectedLevel, DOKLevel
                ) VALUES (
                  :teacher_id, :student_id, :vocab_id, 'Linked probe', 'worksheet', 1,
                  0, 'dogs', 2, '1'
                )
                """
            ),
            {"teacher_id": teacher_id, "student_id": student_id, "vocab_id": vocab_id},
        )
    removed = client.delete(f"/api/vocab-lists/{vocab_id}")
    assert removed.status_code == 200
    _expect_one(
        before,
        eventType="vocab_list_deleted",
        teacherid=teacher_id,
        entity="vocabList",
        entityid=vocab_id,
        statusCode=200,
        success=1,
    )
    assert _scalar(
        "SELECT vocabListid FROM worksheets WHERE title = 'Linked probe'"
    ) is None
    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM worksheets WHERE title = 'Linked probe'"))

    spare = client.post("/api/vocab-lists", json={
        "student_id": student_id,
        "list_name": "Unused probe",
        "words": ["dog"],
    })
    spare_id = spare.get_json()["vocab_list"]["vocab_list_id"]
    before = _max_event_id()
    unused = client.delete(f"/api/vocab-lists/{spare_id}")
    assert unused.status_code == 200
    _expect_one(
        before,
        eventType="vocab_list_deleted",
        entity="vocabList",
        entityid=spare_id,
        success=1,
    )

    other_id = _scalar(
        """
        SELECT vl.vocabListid
        FROM vocabList vl
        JOIN Students s ON s.studentid = vl.studentid
        JOIN class c ON c.classid = s.classid
        WHERE c.teacherid <> :teacher_id
        ORDER BY vl.vocabListid
        LIMIT 1
        """,
        {"teacher_id": teacher_id},
    )
    before = _max_event_id()
    blocked = client.delete(f"/api/vocab-lists/{int(other_id)}")
    assert blocked.status_code == 404
    _expect_one(
        before,
        eventType="vocab_list_deleted",
        teacherid=teacher_id,
        entity="vocabList",
        entityid=int(other_id),
        statusCode=404,
        errorMessage="not_found",
        success=0,
    )
    assert _scalar(
        "SELECT COUNT(*) FROM vocabList WHERE vocabListid = :vocab_id",
        {"vocab_id": int(other_id)},
    ) == 1

    before = _max_event_id()
    generated = client.post("/generate", json={
        "grade": "4",
        "reading_level": "2",
        "interests": "dogs",
        "student_id": student_id,
    })
    assert generated.status_code == 200
    generation_id = generated.get_json()["generation_id"]
    sheet_id = _scalar(
        "SELECT sheetid FROM versions WHERE generationid = :generation_id",
        {"generation_id": generation_id},
    )
    _expect_one(
        before,
        eventType="worksheet_generated",
        teacherid=teacher_id,
        entity="worksheet",
        entityid=sheet_id,
        statusCode=200,
        success=1,
    )

    monkeypatch.setattr(app_module.cache, "worksheet_quota_used", lambda teacher_id: True)
    before = _max_event_id()
    limited = client.post("/generate", json={
        "grade": "4",
        "reading_level": "2",
        "interests": "dogs",
    })
    assert limited.status_code == 429
    _expect_one(
        before,
        eventType="worksheet_generated",
        teacherid=teacher_id,
        entity=None,
        entityid=None,
        statusCode=429,
        errorMessage="quota_exceeded",
        success=0,
    )

    before = _max_event_id()
    download = client.post(f"/worksheets/download/{generation_id}")
    assert download.status_code == 200
    version_id = _scalar(
        "SELECT versionid FROM versions WHERE generationid = :generation_id",
        {"generation_id": generation_id},
    )
    _expect_one(
        before,
        eventType="worksheet_downloaded",
        teacherid=teacher_id,
        entity="version",
        entityid=version_id,
        statusCode=200,
        success=1,
    )

    before = _max_event_id()
    missing = client.post("/worksheets/download/d4444444444444444444444444444444")
    assert missing.status_code == 404
    _expect_one(
        before,
        eventType="worksheet_downloaded",
        teacherid=teacher_id,
        statusCode=404,
        errorMessage="not_found",
        success=0,
    )

    before = _max_event_id()
    logged_out = client.get("/logout")
    assert logged_out.status_code == 302
    _expect_one(before, eventType="logout", teacherid=teacher_id, statusCode=200, success=1)

    passage = _scalar(
        """
        SELECT COUNT(*) FROM Events
        WHERE COALESCE(errorMessage, '') LIKE '%A dog ran%'
           OR COALESCE(errorMessage, '') LIKE '%@%'
           OR COALESCE(errorMessage, '') LIKE '%password123%'
           OR COALESCE(errorMessage, '') LIKE '%not-the-password%'
           OR COALESCE(entity, '') LIKE '%@%'
        """
    )
    assert passage == 0
    assert _scalar(
        """
        SELECT COUNT(*) FROM Events
        WHERE eventType = 'worksheet_generated' AND success = 1
        """
    ) == _scalar("SELECT COUNT(*) FROM worksheets")
    assert _scalar(
        """
        SELECT COUNT(*) FROM Events
        WHERE eventType = 'worksheet_downloaded' AND success = 1
        """
    ) == _scalar("SELECT COUNT(*) FROM downloads")


def test_generation_timeout_and_unverified_login(mysql, monkeypatch):
    maya = db.find_teacher_by_email("maya.chen@granted.local")
    if maya is None:
        pytest.skip("seed_dev.sql is not loaded")
    monkeypatch.setattr(app_module.cache, "worksheet_quota_used", lambda teacher_id: False)
    monkeypatch.setattr(app_module.billing, "has_pro_access", lambda teacher_id: True)

    def timed_out(*_args, **_kwargs):
        raise TimeoutError("llm timed out")

    monkeypatch.setattr(app_module.llm_pipeline, "generate_full_worksheet", timed_out)
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    before = _max_event_id()
    expired = client.post("/generate", json={
        "grade": "4",
        "reading_level": "2",
        "interests": "dogs",
    })
    assert expired.status_code == 401
    _expect_one(
        before,
        eventType="worksheet_generated",
        teacherid=None,
        statusCode=401,
        errorMessage="session_expired",
        success=0,
    )

    with client.session_transaction() as sess:
        sess["teacher_id"] = maya["teacher_id"]
        sess["last_touch"] = 10**12

    before = _max_event_id()
    response = client.post("/generate", json={
        "grade": "4",
        "reading_level": "2",
        "interests": "dogs",
    })
    assert response.status_code == 504
    _expect_one(
        before,
        eventType="worksheet_generated",
        teacherid=maya["teacher_id"],
        entity=None,
        entityid=None,
        statusCode=504,
        errorMessage="llm_timeout",
        success=0,
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("foundry rejected the request")

    monkeypatch.setattr(app_module.llm_pipeline, "build_pdf_bytes", boom)
    before = _max_event_id()
    updated = client.post("/update-pdf", json={
        "title": "Sample",
        "story": "A short story.",
        "questions": {"q1": "Who ran?", "q2": "What happened?", "q3": "Where?", "q4": "When?", "q5": "Why?"},
        "image_base64": "abc",
    })
    assert updated.status_code == 500
    _expect_one(
        before,
        eventType="worksheet_updated",
        teacherid=maya["teacher_id"],
        entity=None,
        entityid=None,
        statusCode=500,
        errorMessage="update_failed",
        success=0,
    )

    unverified_email = f"una.{_max_event_id()}@granted.local"
    with db.engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO Teachers (fname, lname, email, password, verified, country, state, city, school, district)
                VALUES ('Una', 'Verified', :email, 'password123', 0, 'United States', 'Pennsylvania', 'Philadelphia', 'Lincoln Elementary', 'Metro District')
                """
            ),
            {"email": unverified_email},
        )
    monkeypatch.setattr(app_module, "_issue_verification", lambda teacher, host: True)
    before = _max_event_id()
    unverified = client.post("/login", json={
        "email": unverified_email,
        "password": "password123",
    })
    assert unverified.status_code == 403
    row = _expect_one(
        before,
        eventType="login",
        statusCode=403,
        errorMessage="email_not_verified",
        success=0,
    )
    assert row["teacherid"] is not None
    assert unverified_email not in str(row["errorMessage"])
    with db.engine.begin() as conn:
        conn.execute(
            text("DELETE FROM Teachers WHERE email = :email"),
            {"email": unverified_email},
        )


def test_events_record_the_open_pilot(mysql):
    priya = db.find_teacher_by_email("priya.nair@granted.local")
    maya = db.find_teacher_by_email("maya.chen@granted.local")
    sam = db.find_teacher_by_email("sam.ortiz@granted.local")
    if not all((priya, maya, sam)):
        pytest.skip("seed_dev.sql is not loaded")
    before = _max_event_id()
    events.log_event(events.LOGOUT, teacher_id=priya["teacher_id"], success=True, status_code=200)
    events.log_event(events.LOGOUT, teacher_id=maya["teacher_id"], success=True, status_code=200)
    events.log_event(events.LOGOUT, teacher_id=sam["teacher_id"], success=True, status_code=200)
    events.log_event(events.LOGIN, success=False, status_code=401, error_message="unknown_email")
    rows = _rows_after(before)
    assert [row["pilotid"] for row in rows] == [1, None, 2, None]
    seeded = _scalar(
        """
        SELECT COUNT(*) FROM Events
        WHERE teacherid = :teacher_id AND pilotid = 1 AND eventid <= :before
        """,
        {"teacher_id": priya["teacher_id"], "before": before},
    )
    assert seeded == 4
    assert _scalar(
        "SELECT COUNT(*) FROM Events WHERE teacherid = :teacher_id AND pilotid = 2 AND eventid <= :before",
        {"teacher_id": sam["teacher_id"], "before": before},
    ) == 4


def test_idle_session_ends_without_a_logout_event(mysql):
    maya = db.find_teacher_by_email("maya.chen@granted.local")
    if maya is None:
        pytest.skip("seed_dev.sql is not loaded")
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    signed_in = client.post("/login", json={
        "email": "maya.chen@granted.local",
        "password": "password123",
    })
    assert signed_in.status_code == 200
    with client.session_transaction() as sess:
        sess["last_activity"] = time.time() - (31 * 60)
    before = _max_event_id()
    page = client.get("/worksheet")
    assert page.status_code == 302
    assert "expired=1" in page.headers["Location"]
    assert _rows_after(before) == []
    again = client.get("/worksheet")
    assert again.status_code == 302


def test_failed_event_insert_does_not_block_the_action(mysql, monkeypatch):
    maya = db.find_teacher_by_email("maya.chen@granted.local")
    if maya is None:
        pytest.skip("seed_dev.sql is not loaded")
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["teacher_id"] = maya["teacher_id"]
        sess["last_touch"] = 10**12

    with db.engine.begin() as conn:
        conn.exec_driver_sql("RENAME TABLE Events TO Events_broken")
    try:
        created = client.post("/api/classes", json={
            "class_type": "standard",
            "name": "Still saved",
            "subject": "English",
            "grade_level": 4,
            "school_year": "2026-2027",
        })
    finally:
        with db.engine.begin() as conn:
            conn.exec_driver_sql("RENAME TABLE Events_broken TO Events")

    assert created.status_code == 200
    class_id = created.get_json()["class"]["class_id"]
    assert _scalar(
        "SELECT COUNT(*) FROM class WHERE classid = :class_id",
        {"class_id": class_id},
    ) == 1
    assert _scalar(
        "SELECT COUNT(*) FROM Events WHERE entity = 'class' AND entityid = :class_id",
        {"class_id": class_id},
    ) == 0
