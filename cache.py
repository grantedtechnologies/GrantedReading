import base64
import json
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse

from dotenv import load_dotenv
import redis

load_dotenv(override=True)

WORKSHEET_TTL_SECONDS = 24 * 60 * 60
WORKSHEET_KEY = "worksheet:{teacher_id}"

# How long a generated-but-not-downloaded worksheet can still be saved.
PENDING_TTL_SECONDS = 24 * 60 * 60
PENDING_KEY = "pending_worksheet:{generation_id}"
SAVE_LOCK_KEY = "saving_worksheet:{generation_id}"
SAVE_LOCK_SECONDS = 120

# Stripe retries a webhook for about 3 days. The key outlives that window,
# then expires so Redis does not keep every event id forever.
WEBHOOK_EVENT_TTL_SECONDS = 4 * 24 * 60 * 60
WEBHOOK_EVENT_KEY = "stripe_event:{event_id}"

_client = None


def _env(*names, default=""):
    for name in names:
        value = (os.getenv(name) or "").strip().strip("'\"")
        if value:
            return value
    return default


def _redis_url():
    """Use REDIS_URL when it points at a real server.

    A leftover redis://localhost URL does not hide REDIS_HOST. On Railway the
    private REDIS_URL is the one that resolves, so it wins there too.
    """
    url = _env("REDIS_URL")
    parsed = urlparse(url) if url else None
    url_is_local = parsed is None or parsed.hostname in (None, "localhost", "127.0.0.1")
    if url and not url_is_local:
        return url
    host = _env("REDIS_HOST", "REDISHOST")
    if host:
        scheme = "redis" if host.endswith(".railway.internal") else "rediss"
        user = quote_plus(_env("REDIS_USER", "REDISUSER"))
        password = quote_plus(_env("REDIS_PASSWORD", "REDISPASSWORD"))
        port = _env("REDIS_PORT", "REDISPORT", default="6379")
        auth = f"{user}:{password}@" if user or password else ""
        return f"{scheme}://{auth}{host}:{port}/0"
    return url or "redis://localhost:6379/0"


def _redis():
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            _redis_url(),
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


def stash_pending_worksheet(teacher_id, pdf_bytes, record):
    generation_id = uuid.uuid4().hex
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


def claim_webhook_event(event_id):
    """True the first time this Stripe event id is claimed.

    A retry of the same id gets False and is skipped. If handling then fails,
    release_webhook_event deletes the key so Stripe's retry can run again.
    """
    return bool(_redis().set(
        WEBHOOK_EVENT_KEY.format(event_id=event_id),
        "1",
        nx=True,
        ex=WEBHOOK_EVENT_TTL_SECONDS,
    ))


def release_webhook_event(event_id):
    _redis().delete(WEBHOOK_EVENT_KEY.format(event_id=event_id))
