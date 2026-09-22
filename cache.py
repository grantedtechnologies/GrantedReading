import os
from datetime import datetime, timezone

from dotenv import load_dotenv
import redis

load_dotenv(override=True)

WORKSHEET_TTL_SECONDS = 24 * 60 * 60
WORKSHEET_KEY = "worksheet:{teacher_id}"

_client = None


def _redis():
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
    return _client


def worksheet_quota_used(teacher_id):
    return bool(_redis().get(WORKSHEET_KEY.format(teacher_id=teacher_id)))


def mark_worksheet_used(teacher_id):
    _redis().set(
        WORKSHEET_KEY.format(teacher_id=teacher_id),
        datetime.now(timezone.utc).isoformat(),
        ex=WORKSHEET_TTL_SECONDS,
    )
