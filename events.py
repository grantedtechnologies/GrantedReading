"""One row in Events per teacher action. Inserts never break the request."""
import logging
import re

from sqlalchemy import text

import db

logger = logging.getLogger(__name__)

LOGIN = "login"
LOGOUT = "logout"
CLASS_CREATED = "class_created"
STUDENT_CREATED = "student_created"
VOCAB_LIST_CREATED = "vocab_list_created"
VOCAB_LIST_UPDATED = "vocab_list_updated"
VOCAB_LIST_DELETED = "vocab_list_deleted"
WORKSHEET_GENERATED = "worksheet_generated"
WORKSHEET_DOWNLOADED = "worksheet_downloaded"

EVENT_TYPES = frozenset({
    LOGIN,
    LOGOUT,
    CLASS_CREATED,
    STUDENT_CREATED,
    VOCAB_LIST_CREATED,
    VOCAB_LIST_UPDATED,
    VOCAB_LIST_DELETED,
    WORKSHEET_GENERATED,
    WORKSHEET_DOWNLOADED,
})

_REASON = re.compile(r"^[a-z0-9_]{1,500}$")


def _reason(success, error_message):
    if success:
        return None
    code = (error_message or "").strip()[:500]
    if not _REASON.fullmatch(code):
        return "unknown_error"
    return code


def log_event(
    event_type,
    teacher_id=None,
    entity=None,
    entity_id=None,
    success=True,
    status_code=200,
    error_message=None,
):
    """Record one action on its own connection. Never raises."""
    try:
        if event_type not in EVENT_TYPES:
            logger.error("Refusing unknown event type %s", event_type)
            return
        if (entity is None) != (entity_id is None):
            logger.error("Event %s needs both entity and entity id, or neither", event_type)
            return
        reason = _reason(bool(success), error_message)
        teacher = None if teacher_id in (None, "") else int(teacher_id)
        with db.engine.begin() as conn:
            conn.execute(text("SET time_zone = '+00:00'"))
            pilot_id = None
            if teacher is not None:
                pilot_id = conn.execute(
                    text(
                        """
                        SELECT p.pilotid
                        FROM subscriptions s
                        JOIN Pilot p ON p.pilotid = s.pilotid
                        WHERE s.teacherid = :teacher_id
                          AND s.pilotid IS NOT NULL
                          AND UTC_TIMESTAMP() BETWEEN p.startTime AND p.endTime
                        ORDER BY p.endTime DESC, p.pilotid DESC
                        LIMIT 1
                        """
                    ),
                    {"teacher_id": teacher},
                ).scalar()
            conn.execute(
                text(
                    """
                    INSERT INTO Events
                      (eventType, teacherid, pilotid, entity, entityid, statusCode,
                       errorMessage, success)
                    VALUES
                      (:event_type, :teacher_id, :pilot_id, :entity, :entity_id,
                       :status_code, :error_message, :success)
                    """
                ),
                {
                    "event_type": event_type,
                    "teacher_id": teacher,
                    "pilot_id": None if pilot_id is None else int(pilot_id),
                    "entity": entity,
                    "entity_id": None if entity_id in (None, "") else int(entity_id),
                    "status_code": None if status_code in (None, "") else int(status_code),
                    "error_message": reason,
                    "success": 1 if success else 0,
                },
            )
    except Exception:
        logger.exception("Could not record %s", event_type)
