"""Versions are written on generate, edit, and image regen."""
import pytest
from sqlalchemy import text

import app as app_module
import db


PARENT_GENERATION = "a" * 32


def _teacher_mocks(monkeypatch):
    monkeypatch.setattr(app_module.db, "find_teacher_by_id", lambda teacher_id: {
        "teacher_id": 1,
        "first_name": "A",
        "last_name": "B",
        "verified": True,
    })
    monkeypatch.setattr(app_module.db, "list_worksheets_for_teacher", lambda teacher_id: [])
    monkeypatch.setattr(app_module.db, "list_downloaded_versions", lambda teacher_id, limit=None: [])
    monkeypatch.setattr(app_module.db, "list_classes_for_teacher", lambda teacher_id: [])
    monkeypatch.setattr(app_module.billing, "has_pro_access", lambda teacher_id: True)
    monkeypatch.setattr(app_module.billing, "is_paid_pro", lambda teacher_id: True)
    monkeypatch.setattr(app_module.db, "active_pilot_for_teacher", lambda teacher_id: None)


def _skip_without_db():
    try:
        with db.engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception as exc:
        pytest.skip(f"MySQL is not available: {exc}")


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


def test_insert_worksheet_leaves_blob_keys_null_until_download():
    _skip_without_db()
    maya = db.find_teacher_by_email("maya.chen@granted.local")
    if maya is None:
        pytest.skip("seed_dev.sql is not loaded")
    teacher_id = maya["teacher_id"]
    sheet_id = None
    try:
        initial = db.insert_worksheet(teacher_id, "d" * 32, _base_record())
        sheet_id = initial["sheetid"]
        assert initial["source"] == "initial"
        assert initial["pdfBlobKey"] is None
        assert initial["imageBlobKey"] is None

        edited = db.insert_worksheet(
            teacher_id,
            "e" * 32,
            _base_record(
                sheetid=initial["sheetid"],
                parentVersionid=initial["versionid"],
                source="edit",
                title="Cats edited",
            ),
        )
        assert edited["sheetid"] == initial["sheetid"]
        assert edited["parentVersionid"] == initial["versionid"]
        assert edited["source"] == "edit"
        assert edited["pdfBlobKey"] is None

        regen = db.insert_worksheet(
            teacher_id,
            "f" * 32,
            _base_record(
                sheetid=initial["sheetid"],
                parentVersionid=edited["versionid"],
                source="regen_image",
                title="Cats illustrated",
            ),
        )
        assert regen["sheetid"] == initial["sheetid"]
        assert regen["parentVersionid"] == edited["versionid"]
        assert regen["source"] == "regen_image"
        assert regen["pdfBlobKey"] is None
        downloaded_ids = {
            item["versionid"] for item in db.list_downloaded_versions(teacher_id)
        }
        assert initial["versionid"] not in downloaded_ids
        assert edited["versionid"] not in downloaded_ids
        assert regen["versionid"] not in downloaded_ids
    finally:
        ids = [sheet_id] if sheet_id is not None else []
        with db.engine.begin() as conn:
            for gid in ("d" * 32, "e" * 32, "f" * 32):
                row = conn.execute(
                    text("SELECT sheetid FROM versions WHERE generationid = :gid"),
                    {"gid": gid},
                ).first()
                if row:
                    ids.append(row[0])
            for sid in set(ids):
                conn.execute(
                    text("UPDATE versions SET parentVersionid = NULL WHERE sheetid = :id"),
                    {"id": sid},
                )
                conn.execute(text("DELETE FROM versions WHERE sheetid = :id"), {"id": sid})
                conn.execute(text("DELETE FROM worksheets WHERE sheetid = :id"), {"id": sid})


def test_restash_persists_regen_image_as_child(monkeypatch):
    parent = {
        "sheetid": 10,
        "versionid": 3,
        "expectedLevel": 2,
        "trueLevel": 2,
        "targetDOKLevel": "1",
        "dokRequest": None,
        "sheettype": "worksheet",
        "original": True,
        "informational": False,
        "interest": "cats",
        "phonics": None,
        "studentid": None,
        "vocabListid": None,
        "content": '{"title":"Cats","story":"A cat sat.","questions":{}}',
    }
    inserted = {}

    monkeypatch.setattr(app_module, "_verified_teacher", lambda: {"teacher_id": 1})
    monkeypatch.setattr(
        app_module.db, "find_worksheet_by_generation", lambda *args, **kwargs: parent
    )

    def fake_insert(teacher_id, generation_id, record, store_pdf=None):
        inserted["teacher_id"] = teacher_id
        inserted["generation_id"] = generation_id
        inserted["record"] = record
        inserted["store_pdf"] = store_pdf
        return {"sheetid": 10, "versionid": 4, "generationid": generation_id}

    monkeypatch.setattr(app_module.db, "insert_worksheet", fake_insert)
    monkeypatch.setattr(
        app_module.cache,
        "stash_pending_worksheet",
        lambda teacher_id, pdf_bytes, record, generation_id=None: generation_id,
    )
    monkeypatch.setattr(app_module.cache, "load_pending_worksheet", lambda generation_id: None)

    new_id = app_module._restash_edited_generation(
        {
            "generation_id": PARENT_GENERATION,
            "title": "Cats",
            "story": "A cat sat.",
            "questions": {},
        },
        b"%PDF-1.4",
        "regen_image",
    )

    assert new_id
    assert new_id != PARENT_GENERATION
    assert inserted["store_pdf"] is None
    assert inserted["record"]["source"] == "regen_image"
    assert inserted["record"]["sheetid"] == 10
    assert inserted["record"]["parentVersionid"] == 3


def test_generate_persists_initial_version(monkeypatch):
    _teacher_mocks(monkeypatch)
    inserted = {}

    def fake_generate(*_args, **_kwargs):
        return (
            {
                "title": "Dogs",
                "story": "A dog ran.",
                "questions": {"q1": "Who ran?"},
                "dok_level": 1,
            },
            b"%PDF-1.4",
            "img",
            None,
            {"final": {"estimated_grade": 3}},
            False,
            None,
        )

    def fake_insert(teacher_id, generation_id, record, store_pdf=None):
        inserted["record"] = record
        inserted["store_pdf"] = store_pdf
        return {"sheetid": 1, "versionid": 1, "generationid": generation_id}

    monkeypatch.setattr(app_module.llm_pipeline, "generate_full_worksheet", fake_generate)
    monkeypatch.setattr(app_module.db, "insert_worksheet", fake_insert)
    monkeypatch.setattr(
        app_module.cache,
        "stash_pending_worksheet",
        lambda teacher_id, pdf_bytes, record, generation_id=None: generation_id or "x",
    )
    monkeypatch.setattr(app_module, "_optional_student_id", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module, "_optional_vocab_list_id", lambda *args, **kwargs: None)

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["teacher_id"] = 1
        response = client.post("/generate", json={
            "grade": "3",
            "reading_level": "3",
            "interests": "dogs",
        })

    assert response.status_code == 200
    assert inserted["record"]["source"] == "initial"
    assert inserted["store_pdf"] is None
    assert response.get_json()["generation_id"]


def test_update_pdf_regen_persists_child_version(monkeypatch):
    _teacher_mocks(monkeypatch)
    inserted = {}
    parent = {
        "sheetid": 10,
        "versionid": 3,
        "expectedLevel": 2,
        "trueLevel": 2,
        "targetDOKLevel": "1",
        "dokRequest": None,
        "sheettype": "worksheet",
        "original": True,
        "informational": False,
        "interest": "dogs",
        "phonics": None,
        "studentid": None,
        "vocabListid": None,
        "content": '{"title":"Dogs","story":"A dog ran.","questions":{"q1":"Who ran?"}}',
    }

    def fake_insert(teacher_id, generation_id, record, store_pdf=None):
        inserted["record"] = record
        inserted["store_pdf"] = store_pdf
        return {"sheetid": 10, "versionid": 4, "generationid": generation_id}

    monkeypatch.setattr(app_module.llm_pipeline, "validate_structure", lambda body: None)
    monkeypatch.setattr(
        app_module.llm_pipeline,
        "build_pdf_bytes",
        lambda *args, **kwargs: (b"%PDF-1.4", "new-img"),
    )
    monkeypatch.setattr(
        app_module.db, "find_worksheet_by_generation", lambda *args, **kwargs: parent
    )
    monkeypatch.setattr(app_module.db, "insert_worksheet", fake_insert)
    monkeypatch.setattr(app_module.cache, "load_pending_worksheet", lambda generation_id: None)
    monkeypatch.setattr(
        app_module.cache,
        "stash_pending_worksheet",
        lambda teacher_id, pdf_bytes, record, generation_id=None: generation_id,
    )

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["teacher_id"] = 1
        response = client.post("/update-pdf", json={
            "generation_id": PARENT_GENERATION,
            "title": "Dogs",
            "story": "A dog ran.",
            "questions": {"q1": "Who ran?"},
            "image_feedback": "Make the dog smaller",
        })

    assert response.status_code == 200
    assert inserted["record"]["source"] == "regen_image"
    assert inserted["record"]["parentVersionid"] == 3
    assert inserted["record"]["sheetid"] == 10
    assert inserted["store_pdf"] is None
    assert response.get_json()["generation_id"] != PARENT_GENERATION
