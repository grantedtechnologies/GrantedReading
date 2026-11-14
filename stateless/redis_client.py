"""Shared Redis connection for ephemeral app state.

Auth and other fail-closed paths treat Redis errors as logged-out / unavailable.
Do not fall back to process memory.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote_plus, urlparse

from dotenv import load_dotenv
import redis

load_dotenv()

logger = logging.getLogger(__name__)

_pool: redis.ConnectionPool | None = None
_client: redis.Redis | None = None


def _env(*names, default=""):
    for name in names:
        value = (os.getenv(name) or "").strip().strip("'\"")
        if value:
            return value
    return default


def redis_url() -> str:
    """Resolve REDIS_URL for Railway private host or laptop proxy.

    Prefer a non-local REDIS_URL (Railway private). Otherwise build from
    REDIS_HOST / user / password. Fall back to localhost for laptop dev.
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


def get_redis() -> redis.Redis:
    """Return a process-wide Redis client with decode_responses=True."""
    global _pool, _client
    if _client is not None:
        return _client
    _pool = redis.ConnectionPool.from_url(
        redis_url(),
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
    )
    _client = redis.Redis(connection_pool=_pool)
    return _client


def reset_client() -> None:
    """Drop the cached client (tests)."""
    global _pool, _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
    if _pool is not None:
        try:
            _pool.disconnect()
        except Exception:
            pass
    _pool = None
    _client = None
