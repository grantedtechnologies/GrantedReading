"""Cold-start poll should survive transient status failures."""
import requests

import runpod_flux


def test_poll_retries_request_exception_then_completes(monkeypatch):
    attempts = {"n": 0}

    class FakeResp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    def fake_request(method, url, timeout=None, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise requests.RequestException("temporary glitch")
        return FakeResp(
            200,
            {
                "id": "job-1",
                "status": "COMPLETED",
                "output": {"data": [{"b64_json": "abc"}]},
                "delayTime": 1,
                "executionTime": 2,
            },
        )

    monkeypatch.setattr(runpod_flux.requests, "request", fake_request)
    started = runpod_flux.time.perf_counter()
    data, err = runpod_flux._poll_until_terminal(
        "job-1",
        headers={},
        started=started,
        deadline=started + 5,
        data={"id": "job-1", "status": "IN_QUEUE"},
    )
    assert err is None
    assert data["status"] == "COMPLETED"
    assert attempts["n"] >= 2
