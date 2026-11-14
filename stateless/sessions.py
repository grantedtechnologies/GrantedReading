"""Redis-backed login sessions.

Cookie holds only an opaque token. Redis stores sess:{sha256(token)}.
Fail closed: Redis errors mean the user is treated as logged out.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from typing import Any

from redis.exceptions import RedisError

from .redis_client import get_redis

logger = logging.getLogger(__name__)

SESSION_IDLE_SECONDS = int(os.getenv("SESSION_IDLE_SECONDS") or "1800")
SESSION_ABSOLUTE_SECONDS = int(os.getenv("SESSION_ABSOLUTE_SECONDS") or str(12 * 60 * 60))
MYSQL_ACTIVITY_SECONDS = 5 * 60
PENDING_CHECKOUT_SECONDS = 60 * 60

# __Host- requires Secure + Path=/ + no Domain. Plain http local cannot use it.
SESSION_COOKIE_SECURE = (os.getenv("SESSION_COOKIE_SECURE") or "").strip().lower() not in (
    "0",
    "false",
    "no",
)
# Default secure on Railway; allow override. If unset and not Railway, false for local http.
if "SESSION_COOKIE_SECURE" not in os.environ:
    SESSION_COOKIE_SECURE = bool(
        os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID")
    )

COOKIE_NAME = (
    "__Host-granted_session" if SESSION_COOKIE_SECURE else "granted_session"
)
PENDING_COOKIE_NAME = (
    "__Host-granted_pending" if SESSION_COOKIE_SECURE else "granted_pending"
)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _sess_key(token_hash: str) -> str:
    return f"sess:{token_hash}"


def _user_sessions_key(teacher_id: int) -> str:
    return f"user_sessions:{int(teacher_id)}"


def _pending_key(token_hash: str) -> str:
    return f"pending_checkout:{token_hash}"


def token_from_request(request) -> str | None:
    raw = request.cookies.get(COOKIE_NAME) or ""
    raw = raw.strip()
    return raw or None


def pending_token_from_request(request) -> str | None:
    raw = (request.cookies.get(PENDING_COOKIE_NAME) or "").strip()
    return raw or None


def create_session(
    teacher_id: int,
    *,
    mysql_session_id: str,
    user_agent: str = "",
    teacher_email: str = "",
    teacher_name: str = "",
) -> str:
    """Issue a new opaque token and store the hashed session in Redis."""
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    now = int(time.time())
    absolute = now + SESSION_ABSOLUTE_SECONDS
    ttl = min(SESSION_IDLE_SECONDS, max(1, absolute - now))
    ua = (user_agent or "")[:200]
    client = get_redis()
    pipe = client.pipeline()
    pipe.hset(
        _sess_key(token_hash),
        mapping={
            "teacher_id": str(int(teacher_id)),
            "created_at": str(now),
            "last_seen": str(now),
            "absolute_expires_at": str(absolute),
            "mysql_session_id": mysql_session_id or "",
            "user_agent": ua,
            "teacher_email": (teacher_email or "")[:255],
            "teacher_name": (teacher_name or "")[:255],
            "last_mysql_touch": str(now),
        },
    )
    pipe.expire(_sess_key(token_hash), ttl)
    pipe.sadd(_user_sessions_key(teacher_id), token_hash)
    pipe.execute()
    _prune_user_sessions(teacher_id)
    return token


def load_and_touch(token: str) -> dict[str, Any] | None:
    """Load session, enforce absolute expiry, refresh idle TTL. None if invalid."""
    if not token:
        return None
    token_hash = _hash_token(token)
    key = _sess_key(token_hash)
    client = get_redis()
    try:
        data = client.hgetall(key)
    except RedisError:
        logger.exception("Redis session load failed")
        raise
    if not data:
        return None
    now = int(time.time())
    try:
        absolute = int(data.get("absolute_expires_at") or 0)
        teacher_id = int(data.get("teacher_id") or 0)
    except (TypeError, ValueError):
        client.delete(key)
        return None
    if teacher_id <= 0 or now >= absolute:
        client.delete(key)
        if teacher_id > 0:
            try:
                client.srem(_user_sessions_key(teacher_id), token_hash)
            except RedisError:
                logger.exception("Redis session index cleanup failed")
        return None

    ttl = min(SESSION_IDLE_SECONDS, max(1, absolute - now))
    pipe = client.pipeline()
    pipe.hset(key, mapping={"last_seen": str(now)})
    pipe.expire(key, ttl)
    pipe.execute()
    data["last_seen"] = str(now)
    data["token_hash"] = token_hash
    return data


def destroy_session(token: str | None) -> dict[str, Any] | None:
    """Delete one session. Returns the former hash fields if present."""
    if not token:
        return None
    token_hash = _hash_token(token)
    key = _sess_key(token_hash)
    client = get_redis()
    data = client.hgetall(key)
    pipe = client.pipeline()
    pipe.delete(key)
    if data and data.get("teacher_id"):
        try:
            pipe.srem(_user_sessions_key(int(data["teacher_id"])), token_hash)
        except (TypeError, ValueError):
            pass
    pipe.execute()
    return data or None


def destroy_all_sessions(teacher_id: int) -> int:
    """Revoke every Redis session for a teacher. Returns number deleted."""
    client = get_redis()
    index = _user_sessions_key(teacher_id)
    hashes = list(client.smembers(index) or [])
    if not hashes:
        return 0
    pipe = client.pipeline()
    for token_hash in hashes:
        pipe.delete(_sess_key(token_hash))
    pipe.delete(index)
    pipe.execute()
    return len(hashes)


def _prune_user_sessions(teacher_id: int) -> None:
    """Drop index members whose sess: key is gone."""
    client = get_redis()
    index = _user_sessions_key(teacher_id)
    hashes = list(client.smembers(index) or [])
    if not hashes:
        return
    pipe = client.pipeline()
    for token_hash in hashes:
        pipe.exists(_sess_key(token_hash))
    exists_flags = pipe.execute()
    stale = [h for h, ok in zip(hashes, exists_flags) if not ok]
    if stale:
        client.srem(index, *stale)


def should_touch_mysql(data: dict[str, Any]) -> bool:
    """True when MySQL endedAt should be bumped (at most every 5 minutes)."""
    try:
        last = int(data.get("last_mysql_touch") or 0)
    except (TypeError, ValueError):
        return True
    return (int(time.time()) - last) >= MYSQL_ACTIVITY_SECONDS


def mark_mysql_touched(token: str) -> None:
    if not token:
        return
    key = _sess_key(_hash_token(token))
    try:
        get_redis().hset(key, "last_mysql_touch", str(int(time.time())))
    except RedisError:
        logger.exception("Redis mark_mysql_touched failed")


def attach_cookie(response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_ABSOLUTE_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="Lax",
        path="/",
    )


def clear_cookie(response) -> None:
    response.set_cookie(
        COOKIE_NAME,
        "",
        max_age=0,
        expires=0,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="Lax",
        path="/",
    )


def create_pending_checkout(teacher_id: int) -> str:
    token = secrets.token_urlsafe(32)
    get_redis().set(
        _pending_key(_hash_token(token)),
        str(int(teacher_id)),
        ex=PENDING_CHECKOUT_SECONDS,
    )
    return token


def load_pending_checkout(token: str | None) -> int | None:
    if not token:
        return None
    raw = get_redis().get(_pending_key(_hash_token(token)))
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def clear_pending_checkout(token: str | None) -> None:
    if not token:
        return
    get_redis().delete(_pending_key(_hash_token(token)))


def attach_pending_cookie(response, token: str) -> None:
    response.set_cookie(
        PENDING_COOKIE_NAME,
        token,
        max_age=PENDING_CHECKOUT_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="Lax",
        path="/",
    )


def clear_pending_cookie(response) -> None:
    response.set_cookie(
        PENDING_COOKIE_NAME,
        "",
        max_age=0,
        expires=0,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="Lax",
        path="/",
    )
