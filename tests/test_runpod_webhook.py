"""Flask webhook route tests for the local RunPod compare app."""
import pytest

import runpod_results
import test_app


@pytest.fixture(autouse=True)
def _reset_store_and_secret(monkeypatch):
    monkeypatch.setattr(test_app, "RUNPOD_WEBHOOK_SECRET", "test-secret")
    runpod_results.STORE.clear()
    yield
    runpod_results.STORE.clear()


@pytest.fixture
def client():
    test_app.app.config["TESTING"] = True
    return test_app.app.test_client()


def test_webhook_wrong_token_404(client):
    response = client.post(
        "/runpod/webhook/wrong",
        json={"id": "abc", "status": "COMPLETED"},
    )
    assert response.status_code == 404
    assert runpod_results.STORE.wait("abc", timeout=0.01) is None


def test_webhook_malformed_400(client):
    response = client.post(
        "/runpod/webhook/test-secret",
        json={"status": "COMPLETED"},
    )
    assert response.status_code == 400


def test_webhook_valid_200_and_stored(client):
    payload = {
        "id": "job-ok",
        "status": "COMPLETED",
        "output": {"data": [{"b64_json": "aaa"}]},
        "delayTime": 10,
        "executionTime": 20,
    }
    response = client.post("/runpod/webhook/test-secret", json=payload)
    assert response.status_code == 200
    entry = runpod_results.STORE.wait("job-ok", timeout=0.1)
    assert entry is not None
    assert entry["payload"]["status"] == "COMPLETED"
    assert entry["payload"]["executionTime"] == 20


def test_webhook_duplicate_not_overwritten(client):
    first = {"id": "job-dup", "status": "COMPLETED", "n": 1}
    second = {"id": "job-dup", "status": "COMPLETED", "n": 2}
    assert client.post("/runpod/webhook/test-secret", json=first).status_code == 200
    assert client.post("/runpod/webhook/test-secret", json=second).status_code == 200
    entry = runpod_results.STORE.wait("job-dup", timeout=0.1)
    assert entry["payload"]["n"] == 1
