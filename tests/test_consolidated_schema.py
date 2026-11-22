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
        "INSERT INTO Pilot (country, state, name, code, startTime, endTime) VALUES ('United States', 'California', 'bad', 'BADWINDOW', '2026-06-01', '2026-01-01')",
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
    assert found["state"] == "California"
    assert found["state_code"] == "CA"


def test_has_pro_access_does_not_use_latest_row(monkeypatch):
    monkeypatch.setattr(db, "paid_pro_exists", lambda teacher_id: False)
    monkeypatch.setattr(db, "active_pilot_exists", lambda teacher_id: True)
    assert billing.has_pro_access(9) is True
    assert billing.is_paid_pro(9) is False


def test_active_pilot_prefers_most_recently_joined(mysql):
    """Display the newest redeem, not the pilot with the latest endTime."""
    from sqlalchemy import text

    priya = db.find_teacher_by_email("priya.nair@granted.local")
    if priya is None:
        pytest.skip("seed_dev.sql is not loaded")
    teacher_id = int(priya["teacher_id"])
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    code_a = f"NEWER{stamp}A"[:32]
    code_b = f"NEWER{stamp}B"[:32]
    pilot_ids = []
    try:
        with db.engine.begin() as conn:
            for name, code, end_days in (
                ("Longer window pilot", code_a, 400),
                ("Shorter window pilot", code_b, 30),
            ):
                result = conn.execute(
                    text(
                        """
                        INSERT INTO Pilot
                          (country, state, stateCode, name, code,
                           startTime, endTime, createdAt)
                        VALUES
                          ('United States', 'California', 'CA', :name, :code,
                           UTC_TIMESTAMP() - INTERVAL 1 DAY,
                           UTC_TIMESTAMP() + INTERVAL :end_days DAY,
                           UTC_TIMESTAMP())
                        """
                    ),
                    {
                        "name": name,
                        "code": code,
                        "end_days": end_days,
                    },
                )
                pilot_ids.append(int(result.lastrowid))

        older = {"pilotid": pilot_ids[0], "name": "Longer window pilot"}
        newer = {"pilotid": pilot_ids[1], "name": "Shorter window pilot"}
        db.join_pilot(teacher_id, older)
        db.join_pilot(teacher_id, newer)
        # Pin stamps after Priya's seed Riverside row (2026-09-01) so endTime
        # cannot decide: longer-window is the older join of these two.
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE subscriptions SET createdAt = '2026-11-10 00:00:00' "
                    "WHERE teacherid = :t AND pilotid = :p"
                ),
                {"t": teacher_id, "p": pilot_ids[0]},
            )
            conn.execute(
                text(
                    "UPDATE subscriptions SET createdAt = '2026-11-20 00:00:00' "
                    "WHERE teacherid = :t AND pilotid = :p"
                ),
                {"t": teacher_id, "p": pilot_ids[1]},
            )
        current = db.active_pilot_for_teacher(teacher_id)
        assert current is not None
        assert int(current["pilotid"]) == pilot_ids[1]

        # Re-redeem bumps createdAt and becomes the displayed pilot.
        with db.engine.connect() as conn:
            before = conn.execute(
                text(
                    "SELECT createdAt FROM subscriptions "
                    "WHERE teacherid = :t AND pilotid = :p"
                ),
                {"t": teacher_id, "p": pilot_ids[0]},
            ).scalar()
        db.join_pilot(teacher_id, older)
        with db.engine.connect() as conn:
            after = conn.execute(
                text(
                    "SELECT createdAt FROM subscriptions "
                    "WHERE teacherid = :t AND pilotid = :p"
                ),
                {"t": teacher_id, "p": pilot_ids[0]},
            ).scalar()
        assert after > before
        current = db.active_pilot_for_teacher(teacher_id)
        assert current is not None
        assert int(current["pilotid"]) == pilot_ids[0]
    finally:
        with db.engine.begin() as conn:
            for pilot_id in pilot_ids:
                conn.execute(
                    text("DELETE FROM subscriptions WHERE teacherid = :t AND pilotid = :p"),
                    {"t": teacher_id, "p": pilot_id},
                )
                conn.execute(
                    text("DELETE FROM Pilot WHERE pilotid = :p"),
                    {"p": pilot_id},
                )
