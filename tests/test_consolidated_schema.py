"""Schema CHECKs, derived Pro access, and signup pilot codes."""
from datetime import datetime, timedelta

import pytest

import billing
import db


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


def test_mysql_is_80_or_newer(mysql):
    with db.engine.connect() as conn:
        version = conn.exec_driver_sql("SELECT VERSION()").scalar()
    assert version
    major = int(str(version).split(".", 1)[0])
    assert major >= 8


def test_seed_personas_have_derived_access(mysql):
    maya = db.find_teacher_by_email("maya.chen@granted.local")
    luis = db.find_teacher_by_email("luis.rivera@granted.local")
    priya = db.find_teacher_by_email("priya.nair@granted.local")
    sam = db.find_teacher_by_email("sam.ortiz@granted.local")
    if not all((maya, luis, priya, sam)):
        pytest.skip("seed_dev.sql is not loaded")
    assert billing.is_paid_pro(maya["teacher_id"]) is False
    assert billing.has_pro_access(maya["teacher_id"]) is False
    assert billing.is_paid_pro(luis["teacher_id"]) is True
    assert billing.has_pro_access(luis["teacher_id"]) is True
    assert billing.is_paid_pro(priya["teacher_id"]) is False
    assert billing.has_pro_access(priya["teacher_id"]) is True
    assert billing.is_paid_pro(sam["teacher_id"]) is False
    assert billing.has_pro_access(sam["teacher_id"]) is False


def test_seed_has_no_worksheet_rows(mysql):
    maya = db.find_teacher_by_email("maya.chen@granted.local")
    if maya is None:
        pytest.skip("seed_dev.sql is not loaded")
    with db.engine.connect() as conn:
        events = conn.exec_driver_sql("SELECT COUNT(*) FROM Events").scalar()
    if events != 36:
        pytest.skip("seed_dev.sql is not the current database")
    with db.engine.connect() as conn:
        sheets = conn.exec_driver_sql("SELECT COUNT(*) FROM worksheets").scalar()
        versions = conn.exec_driver_sql("SELECT COUNT(*) FROM versions").scalar()
        downloads = conn.exec_driver_sql("SELECT COUNT(*) FROM downloads").scalar()
    assert sheets == 0
    assert versions == 0
    assert downloads == 0


def test_checks_reject_bad_rows(mysql):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError, OperationalError

    sheet = (
        "INSERT INTO worksheets (teacherid, title, sheettype, original, informational, "
        "interest, expectedLevel) VALUES (1, 'check', 'worksheet', 1, 0, '', 2)"
    )
    cases = [
        "INSERT INTO versions (generationid, sheetid, source, title, content, trueLevel, rating) VALUES ('e5555555555555555555555555555555', LAST_INSERT_ID(), 'initial', 'x', '{}', 2, 0)",
        "INSERT INTO versions (generationid, sheetid, source, title, content, trueLevel, rating) VALUES ('e6666666666666666666666666666666', LAST_INSERT_ID(), 'initial', 'x', '{}', 2, 6)",
        "INSERT INTO Pilot (name, school, code, startTime, endTime) VALUES ('bad', 'x', 'BADWINDOW', '2026-06-01', '2026-01-01')",
        "INSERT INTO subscriptions (teacherid, pilotid, stripeCustomerId, stripeSubscriptionId, price, status) VALUES (1, 1, 'cus_x', 'sub_x', 0, 'active')",
        "INSERT INTO subscriptions (teacherid, stripeCustomerId, stripeSubscriptionId, price, status) VALUES (1, NULL, NULL, 5.99, 'active')",
        "INSERT INTO worksheets (teacherid, title, sheettype, original, informational, interest, expectedLevel, DOKLevel, DOKRequest) VALUES (1, 'x', 'worksheet', 1, 0, '', 2, 'custom', NULL)",
        "INSERT INTO worksheets (teacherid, title, sheettype, original, informational, interest, expectedLevel, DOKLevel, DOKRequest) VALUES (1, 'x', 'worksheet', 1, 0, '', 2, '1', CAST('{\"a\":true}' AS JSON))",
        "INSERT INTO words (word) VALUES ('orphan')",
    ]
    with db.engine.connect() as conn:
        for statement in cases:
            with pytest.raises((IntegrityError, OperationalError)):
                with conn.begin():
                    if "LAST_INSERT_ID()" in statement:
                        conn.exec_driver_sql(sheet)
                    conn.exec_driver_sql(statement)


def test_pilot_lookup_is_case_insensitive(mysql):
    found = db.find_pilot_by_code("riverside2026")
    if found is None:
        pytest.skip("seed_dev.sql is not loaded")
    assert found["code"] == "RIVERSIDE2026"


def test_has_pro_access_does_not_use_latest_row(monkeypatch):
    monkeypatch.setattr(db, "paid_pro_exists", lambda teacher_id: False)
    monkeypatch.setattr(db, "active_pilot_exists", lambda teacher_id: True)
    assert billing.has_pro_access(9) is True
    assert billing.is_paid_pro(9) is False
