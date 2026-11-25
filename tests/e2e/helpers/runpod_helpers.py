"""Signed fake Runpod webhook payloads."""

from __future__ import annotations

import json
import os

import requests

from ai_fixtures import FIXTURE_PNG_B64, sign_runpod_webhook


def post_completed(
    base_url: str,
    *,
    job_id: str,
    image_b64: str = FIXTURE_PNG_B64,
    secret: str | None = None,
):
    secret = secret or (os.getenv("RUNPOD_WEBHOOK_SECRET") or "").strip()
    payload = {
        "id": job_id,
        "status": "COMPLETED",
        "output": {"image": f"data:image/png;base64,{image_b64}"},
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Runpod-Signature": sign_runpod_webhook(body, secret=secret),
    }
    return requests.post(
        f"{base_url.rstrip('/')}/webhooks/runpod",
        data=body,
        headers=headers,
        timeout=15,
    )
