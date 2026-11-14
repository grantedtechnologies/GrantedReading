"""Redis login session behavior (fakeredis, no real Redis)."""
from __future__ import annotations

import time

import fakeredis
import pytest
from redis.exceptions import RedisError

from cache import sessions
from tests.conftest_auth import install_fake_redis, login_as


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    install_fake_redis(monkeypatch, client)
    monkeypatch.setattr(sessions, "SESSION_IDLE_SECONDS", 1800)
    monkeypatch.setattr(sessions, "SESSION_ABSOLUTE_SECONDS", 43200)
    return client


def test_login_stores_hash_not_raw_token(fake_redis):
    token = sessions.create_session(7, mysql_session_id="m1", user_agent="ua")
    assert token
    assert not fake_redis.exists(f"sess:{token}")
    token_hash = sessions._hash_token(token)
    assert fake_redis.exists(f"sess:{token_hash}")
    data = fake_redis.hgetall(f"sess:{token_hash}")
    assert data["teacher_id"] == "7"
    assert data["mysql_session_id"] == "m1"
    assert token_hash in fake_redis.smembers("user_sessions:7")


def test_idle_expiry_logs_out(fake_redis, monkeypatch):
    monkeypatch.setattr(sessions, "SESSION_IDLE_SECONDS", 2)
    token = sessions.create_session(3, mysql_session_id="m")
    assert sessions.load_and_touch(token) is not None
    # Expire the Redis key the way idle TTL would.
    fake_redis.expire(f"sess:{sessions._hash_token(token)}", 0)
    time.sleep(0.05)
    assert sessions.load_and_touch(token) is None


def test_absolute_expiry_overrides_activity(fake_redis, monkeypatch):
    monkeypatch.setattr(sessions, "SESSION_ABSOLUTE_SECONDS", 2)
    token = sessions.create_session(4, mysql_session_id="m")
    key = f"sess:{sessions._hash_token(token)}"
    # Pretend absolute expiry is already past even though TTL remains.
    fake_redis.hset(key, "absolute_expires_at", str(int(time.time()) - 1))
    assert sessions.load_and_touch(token) is None
    assert not fake_redis.exists(key)


def test_activity_extends_idle_ttl(fake_redis, monkeypatch):
    monkeypatch.setattr(sessions, "SESSION_IDLE_SECONDS", 30)
    token = sessions.create_session(5, mysql_session_id="m")
    key = f"sess:{sessions._hash_token(token)}"
    fake_redis.expire(key, 5)
    before = fake_redis.ttl(key)
    sessions.load_and_touch(token)
    after = fake_redis.ttl(key)
    assert after > before
    assert after >= 25


def test_logout_deletes_session(fake_redis):
    token = sessions.create_session(8, mysql_session_id="m")
    sessions.destroy_session(token)
    assert sessions.load_and_touch(token) is None
    assert fake_redis.scard("user_sessions:8") == 0


def test_password_change_revokes_all_sessions(fake_redis):
    t1 = sessions.create_session(9, mysql_session_id="a")
    t2 = sessions.create_session(9, mysql_session_id="b")
    other = sessions.create_session(10, mysql_session_id="c")
    assert sessions.destroy_all_sessions(9) == 2
    assert sessions.load_and_touch(t1) is None
    assert sessions.load_and_touch(t2) is None
    assert sessions.load_and_touch(other) is not None


def test_redis_down_means_logged_out(monkeypatch):
    class Boom:
        def hgetall(self, *a, **k):
            raise RedisError("down")

    # sessions imports get_redis by name; patch the binding it uses.
    monkeypatch.setattr(sessions, "get_redis", lambda: Boom())
    with pytest.raises(RedisError):
        sessions.load_and_touch("any-token")


def test_flask_login_sets_cookie(fake_redis, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "llm", app_module.llm)
    monkeypatch.setattr(app_module.db, "create_login_session", lambda *a, **k: None)
    monkeypatch.setattr(app_module.db, "find_teacher_by_email", lambda email: {
        "teacher_id": 11,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@x.test",
        "password": "Secret1!",
        "verified": 1,
    })
    monkeypatch.setattr(app_module.db, "find_teacher_by_id", lambda teacher_id: {
        "teacher_id": 11,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@x.test",
        "verified": 1,
    })
    monkeypatch.setattr(app_module.db, "blocking_survey_for_teacher", lambda teacher_id: None)
    monkeypatch.setattr(app_module.events, "log_event", lambda *a, **k: None)
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    response = client.post("/login", json={"email": "ada@x.test", "password": "Secret1!"})
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    cookie = response.headers.getlist("Set-Cookie")
    assert any(sessions.COOKIE_NAME in c for c in cookie)
    # Only the hash is stored.
    keys = list(fake_redis.scan_iter("sess:*"))
    assert len(keys) == 1
    assert not keys[0].endswith(response.get_json().get("token") or "nope")
