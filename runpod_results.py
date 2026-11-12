"""In-memory RunPod webhook result store for the local Flask test app.

This only works in a single process. Production should use Redis or the DB
instead (multiple gunicorn workers won't share memory).
"""

from __future__ import annotations

import threading
import time

TTL_S = 10 * 60


class ResultStore:
    def __init__(self, ttl_s: float = TTL_S):
        self._ttl_s = ttl_s
        self._cond = threading.Condition()
        # job_id -> {"payload": dict, "received_at": float, "stored_at": float}
        self._items: dict[str, dict] = {}

    def _evict_locked(self) -> None:
        now = time.perf_counter()
        expired = [
            job_id
            for job_id, entry in self._items.items()
            if now - entry["stored_at"] > self._ttl_s
        ]
        for job_id in expired:
            del self._items[job_id]

    def put(self, job_id: str, payload, received_at: float) -> bool:
        """Store a result. First write wins. Returns True if this call stored it."""
        with self._cond:
            self._evict_locked()
            if job_id in self._items:
                self._cond.notify_all()
                return False
            self._items[job_id] = {
                "payload": payload,
                "received_at": received_at,
                "stored_at": time.perf_counter(),
            }
            self._cond.notify_all()
            return True

    def wait(self, job_id: str, timeout: float) -> dict | None:
        """Block until a result exists or timeout. Returns immediately if already stored."""
        deadline = time.perf_counter() + max(0.0, timeout)
        with self._cond:
            while True:
                entry = self._items.get(job_id)
                if entry is not None:
                    return {
                        "payload": entry["payload"],
                        "received_at": entry["received_at"],
                    }
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=remaining)

    def clear(self) -> None:
        with self._cond:
            self._items.clear()


# Shared by Flask webhook handler and runpod_flux.generate() in the same process.
STORE = ResultStore()
