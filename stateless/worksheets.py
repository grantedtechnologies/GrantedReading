"""Daily worksheet quota, pending PDFs, and download save locks."""

import base64
import json
import uuid
from datetime import datetime, timezone

from .redis_client import get_redis

WORKSHEET_TTL_SECONDS = 24 * 60 * 60
WORKSHEET_KEY = "worksheet:{teacher_id}"

# How long a generated-but-not-downloaded worksheet can still be saved.
PENDING_TTL_SECONDS = 24 * 60 * 60
PENDING_KEY = "pending_worksheet:{generation_id}"
SAVE_LOCK_KEY = "saving_worksheet:{generation_id}"
SAVE_LOCK_SECONDS = 120


def _redis():
    return get_redis()


def worksheet_quota_used(teacher_id):
    return bool(_redis().get(WORKSHEET_KEY.format(teacher_id=teacher_id)))


def mark_worksheet_used(teacher_id):
    _redis().set(
        WORKSHEET_KEY.format(teacher_id=teacher_id),
        datetime.now(timezone.utc).isoformat(),
        ex=WORKSHEET_TTL_SECONDS,
    )


def stash_pending_worksheet(teacher_id, pdf_bytes, record, generation_id=None):
    generation_id = generation_id or uuid.uuid4().hex
    payload = {
        "teacher_id": teacher_id,
        "pdf_base64": base64.b64encode(pdf_bytes).decode(),
        "record": record,
    }
    _redis().set(
        PENDING_KEY.format(generation_id=generation_id),
        json.dumps(payload),
        ex=PENDING_TTL_SECONDS,
    )
    return generation_id


def load_pending_worksheet(generation_id):
    raw = _redis().get(PENDING_KEY.format(generation_id=generation_id))
    if not raw:
        return None
    payload = json.loads(raw)
    payload["pdf_bytes"] = base64.b64decode(payload.pop("pdf_base64"))
    return payload


def acquire_save_lock(generation_id):
    return bool(_redis().set(
        SAVE_LOCK_KEY.format(generation_id=generation_id),
        "1",
        nx=True,
        ex=SAVE_LOCK_SECONDS,
    ))


def release_save_lock(generation_id):
    _redis().delete(SAVE_LOCK_KEY.format(generation_id=generation_id))
