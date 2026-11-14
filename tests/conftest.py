"""Shared fixtures: fake Redis so auth/session tests never need a live server."""
from __future__ import annotations

import fakeredis
import pytest

from tests.conftest_auth import install_fake_redis


@pytest.fixture(autouse=True)
def _fake_redis_for_app_tests(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    install_fake_redis(monkeypatch, client)
    yield client
