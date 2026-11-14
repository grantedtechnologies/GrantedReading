"""Helpers for Redis-backed auth in Flask tests."""
from __future__ import annotations

import cache
from stateless import redis_client, sessions


def install_fake_redis(monkeypatch, fake_client):
    monkeypatch.setattr(redis_client, "_client", fake_client)
    monkeypatch.setattr(redis_client, "_pool", object())
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake_client)
    # Bindings imported by name in service modules.
    monkeypatch.setattr(sessions, "get_redis", lambda: fake_client)
    monkeypatch.setattr(cache, "get_redis", lambda: fake_client)


def login_as(client, teacher_id, *, email="t@x.test", name="Test Teacher"):
    token = sessions.create_session(
        int(teacher_id),
        mysql_session_id="test-mysql-session",
        user_agent="pytest",
        teacher_email=email,
        teacher_name=name,
    )
    client.set_cookie(sessions.COOKIE_NAME, token)
    return token
