"""Signed Runpod webhook + MOCK_AI safety."""

from __future__ import annotations

import json

import pytest

from ai_fixtures import FIXTURE_PNG_B64, sign_runpod_webhook


@pytest.fixture
def client(monkeypatch):
    import app as app_module

    monkeypatch.setenv("RUNPOD_WEBHOOK_SECRET", "test-secret")
    claimed = []

    def fake_claim(conn, event_id, event_type):
        key = (event_id, event_type)
        if key in claimed:
            return False
        claimed.append(key)
        return True

    monkeypatch.setattr(app_module.db, "claim_webhook_event", fake_claim)
    # Avoid opening a real DB transaction for claim
    class FakeBegin:
        def __enter__(self):
            return object()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(app_module.db.engine, "begin", lambda: FakeBegin())
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def test_runpod_webhook_accepts_signed_payload(client):
    payload = {
        "id": "job-1",
        "status": "COMPLETED",
        "output": {"image": f"data:image/png;base64,{FIXTURE_PNG_B64}"},
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    response = client.post(
        "/webhooks/runpod",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Runpod-Signature": sign_runpod_webhook(body, secret="test-secret"),
        },
    )
    assert response.status_code == 200
    assert response.get_json()["duplicate"] is False


def test_runpod_webhook_dedupes(client):
    payload = {"id": "job-2", "status": "COMPLETED", "output": {}}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Runpod-Signature": sign_runpod_webhook(body, secret="test-secret"),
    }
    assert client.post("/webhooks/runpod", data=body, headers=headers).status_code == 200
    second = client.post("/webhooks/runpod", data=body, headers=headers)
    assert second.status_code == 200
    assert second.get_json()["duplicate"] is True


def test_runpod_webhook_rejects_bad_signature(client):
    body = b'{"id":"job-3","status":"COMPLETED"}'
    response = client.post(
        "/webhooks/runpod",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Runpod-Signature": "sha256=deadbeef",
        },
    )
    assert response.status_code == 400
