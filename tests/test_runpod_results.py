"""Unit tests for the in-memory RunPod webhook result store."""
import threading
import time

from runpod_results import ResultStore


def test_put_before_wait_returns_immediately():
    store = ResultStore()
    store.put("job-1", {"id": "job-1", "status": "COMPLETED"}, received_at=1.0)
    entry = store.wait("job-1", timeout=1.0)
    assert entry is not None
    assert entry["payload"]["status"] == "COMPLETED"
    assert entry["received_at"] == 1.0


def test_wait_then_put():
    store = ResultStore()
    results = []

    def waiter():
        results.append(store.wait("job-2", timeout=2.0))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)
    store.put("job-2", {"id": "job-2", "status": "COMPLETED"}, received_at=2.5)
    thread.join(timeout=2.0)
    assert results and results[0] is not None
    assert results[0]["payload"]["id"] == "job-2"
    assert results[0]["received_at"] == 2.5


def test_duplicate_put_ignored():
    store = ResultStore()
    assert store.put("job-3", {"status": "COMPLETED", "n": 1}, received_at=1.0) is True
    assert store.put("job-3", {"status": "COMPLETED", "n": 2}, received_at=2.0) is False
    entry = store.wait("job-3", timeout=0.1)
    assert entry["payload"]["n"] == 1
    assert entry["received_at"] == 1.0


def test_wait_timeout_returns_none():
    store = ResultStore()
    assert store.wait("missing", timeout=0.05) is None
