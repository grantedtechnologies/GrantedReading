"""Test DB access and FK-ordered cleanup for e2e+ teachers."""

from __future__ import annotations

from sqlalchemy import text

import db as app_db


def connect():
    return app_db.engine.connect()


def teacher_ids_for_email_pattern(pattern: str = "e2e+%@granted.test") -> list[int]:
    with app_db.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT teacherid FROM Teachers WHERE email LIKE :pattern"),
            {"pattern": pattern},
        ).fetchall()
    return [int(row[0]) for row in rows]


def teacher_by_email(email: str) -> dict | None:
    return app_db.find_teacher_by_email(email)


def delete_teachers(teacher_ids: list[int]) -> None:
    """Delete teachers and dependent rows in FK-safe order."""
    if not teacher_ids:
        return
    ids = [int(tid) for tid in teacher_ids]
    with app_db.engine.begin() as conn:
        for teacher_id in ids:
            version_rows = conn.execute(
                text(
                    """
                    SELECT v.versionid
                    FROM versions v
                    JOIN worksheets w ON w.sheetid = v.sheetid
                    WHERE w.teacherid = :teacher_id
                    """
                ),
                {"teacher_id": teacher_id},
            ).fetchall()
            for (version_id,) in version_rows:
                conn.execute(
                    text("DELETE FROM downloads WHERE versionid = :version_id"),
                    {"version_id": version_id},
                )
            # Clear parentVersionid self-FK before deleting versions
            sheet_rows = conn.execute(
                text("SELECT sheetid FROM worksheets WHERE teacherid = :teacher_id"),
                {"teacher_id": teacher_id},
            ).fetchall()
            for (sheet_id,) in sheet_rows:
                conn.execute(
                    text(
                        "UPDATE versions SET parentVersionid = NULL WHERE sheetid = :sheet_id"
                    ),
                    {"sheet_id": sheet_id},
                )
                conn.execute(
                    text("DELETE FROM versions WHERE sheetid = :sheet_id"),
                    {"sheet_id": sheet_id},
                )
                conn.execute(
                    text("DELETE FROM words WHERE sheetid = :sheet_id"),
                    {"sheet_id": sheet_id},
                )
            conn.execute(
                text("DELETE FROM worksheets WHERE teacherid = :teacher_id"),
                {"teacher_id": teacher_id},
            )

            class_rows = conn.execute(
                text("SELECT classid FROM class WHERE teacherid = :teacher_id"),
                {"teacher_id": teacher_id},
            ).fetchall()
            for (class_id,) in class_rows:
                student_rows = conn.execute(
                    text("SELECT studentid FROM Students WHERE classid = :class_id"),
                    {"class_id": class_id},
                ).fetchall()
                for (student_id,) in student_rows:
                    vocab_rows = conn.execute(
                        text(
                            "SELECT vocabListid FROM vocabList WHERE studentid = :student_id"
                        ),
                        {"student_id": student_id},
                    ).fetchall()
                    for (vocab_list_id,) in vocab_rows:
                        conn.execute(
                            text("DELETE FROM words WHERE vocabListid = :vocab_list_id"),
                            {"vocab_list_id": vocab_list_id},
                        )
                        conn.execute(
                            text("DELETE FROM vocabList WHERE vocabListid = :vocab_list_id"),
                            {"vocab_list_id": vocab_list_id},
                        )
                    conn.execute(
                        text("DELETE FROM Students WHERE studentid = :student_id"),
                        {"student_id": student_id},
                    )
                conn.execute(
                    text("DELETE FROM class WHERE classid = :class_id"),
                    {"class_id": class_id},
                )

            conn.execute(
                text("DELETE FROM Answer WHERE teacherid = :teacher_id"),
                {"teacher_id": teacher_id},
            )
            conn.execute(
                text("DELETE FROM subscriptions WHERE teacherid = :teacher_id"),
                {"teacher_id": teacher_id},
            )
            conn.execute(
                text("DELETE FROM sessions WHERE teacherid = :teacher_id"),
                {"teacher_id": teacher_id},
            )
            conn.execute(
                text("DELETE FROM Events WHERE teacherid = :teacher_id"),
                {"teacher_id": teacher_id},
            )
            conn.execute(
                text("DELETE FROM Teachers WHERE teacherid = :teacher_id"),
                {"teacher_id": teacher_id},
            )


def count_runpod_events(job_id: str) -> int:
    with app_db.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM processed_webhook_events WHERE eventId = :event_id"
            ),
            {"event_id": f"runpod:{job_id}"},
        ).fetchone()
    return int(row[0] if row else 0)


def delete_runpod_events_like(prefix: str = "runpod:mock-") -> None:
    with app_db.engine.begin() as conn:
        conn.execute(
            text("DELETE FROM processed_webhook_events WHERE eventId LIKE :prefix"),
            {"prefix": f"{prefix}%"},
        )
