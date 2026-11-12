"""RunPod Flux image client used by test_app.py.

Env:
  RUNPOD_API_KEY                    — Bearer token (preferred)
  runpod-api-key                    — deprecated fallback for the Bearer token
  RUNPOD_ENDPOINT_ID                — defaults to fxql6eq3fp8df1
  RUNPOD_RESULT_MODE                — webhook | poll (default poll)
  PUBLIC_BASE_URL                   — public ngrok base URL (webhook mode)
  ngrok-endpoint                    — local alias for PUBLIC_BASE_URL
  RUNPOD_WEBHOOK_SECRET             — path token for POST /runpod/webhook/<token>

The endpoint runs the RunPod vLLM-Omni template serving unsloth/FLUX.1-schnell.
Jobs are submitted via POST /run. Results arrive via webhook and/or GET /status/{id}.

Example:
  from runpod_flux import generate
  result = generate("a lighthouse at sunrise", size="1024x1024", seed=42)
  # result["ok"], result["b64"] or result["image_url"], result["elapsed_ms"]
"""

from __future__ import annotations

import base64
import os
import random
import time

import requests
from dotenv import load_dotenv

import runpod_results

load_dotenv()

# Prefer RUNPOD_API_KEY; runpod-api-key is a deprecated fallback for older .env files.
API_KEY = (
    os.getenv("RUNPOD_API_KEY")
    or os.getenv("runpod-api-key")
    or ""
).strip()
ENDPOINT_ID = (os.getenv("RUNPOD_ENDPOINT_ID") or "fxql6eq3fp8df1").strip()
BASE_URL = f"https://api.runpod.ai/v2/{ENDPOINT_ID}"

# PUBLIC_BASE_URL preferred; ngrok-endpoint is a local alias used in this repo's .env.
PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL")
    or os.getenv("ngrok-endpoint")
    or ""
).strip().rstrip("/")
WEBHOOK_SECRET = (os.getenv("RUNPOD_WEBHOOK_SECRET") or "").strip()
RESULT_MODE = (os.getenv("RUNPOD_RESULT_MODE") or "poll").strip().lower()

HTTP_TIMEOUT_S = 60
RATE_LIMIT_ATTEMPTS = 5
WEBHOOK_WAIT_S = 30
# Cold GPU workers can take 60–90s to load FLUX before the job starts.
DEFAULT_TIMEOUT_S = 600
TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"})
# Keep polling through these — common while a worker is still spinning up.
TRANSIENT_STATUS_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})


def _as_data_url_or_b64(value: str):
    text = (value or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return {"image_url": text, "b64": None, "mime": None}
    if text.startswith("data:image/"):
        header, _, payload = text.partition(",")
        mime = "jpeg"
        if "png" in header:
            mime = "png"
        elif "webp" in header:
            mime = "webp"
        return {"image_url": None, "b64": payload, "mime": mime}
    mime = "jpeg"
    try:
        raw = base64.b64decode(text[:32] + "===")
        if raw.startswith(b"\x89PNG"):
            mime = "png"
    except Exception:
        pass
    return {"image_url": None, "b64": text, "mime": mime}


def extract_image(payload):
    """Accept common RunPod / vLLM-Omni worker output shapes."""
    if payload is None:
        raise RuntimeError("RunPod returned no output.")
    if isinstance(payload, list) and payload:
        return extract_image(payload[0])
    if isinstance(payload, str):
        return _as_data_url_or_b64(payload)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected RunPod output type: {type(payload).__name__}")

    for key in ("image", "image_url", "url", "b64_json", "base64"):
        if key in payload and isinstance(payload[key], str) and payload[key].strip():
            return _as_data_url_or_b64(payload[key])

    data = payload.get("data")
    if isinstance(data, str) and data.strip():
        return _as_data_url_or_b64(data)
    if isinstance(data, list) and data:
        return extract_image(data[0])

    images = payload.get("images")
    if isinstance(images, list) and images:
        return extract_image(images[0])
    if isinstance(images, str):
        return _as_data_url_or_b64(images)

    raise RuntimeError(f"Could not find an image in RunPod output keys: {sorted(payload)}")


def _request_with_rate_limit(method: str, url: str, **kwargs) -> requests.Response:
    """HTTP call with exponential backoff + jitter on 429. Does not resubmit jobs."""
    last_response: requests.Response | None = None
    for attempt in range(RATE_LIMIT_ATTEMPTS):
        response = requests.request(method, url, timeout=HTTP_TIMEOUT_S, **kwargs)
        last_response = response
        if response.status_code != 429:
            return response
        if attempt + 1 >= RATE_LIMIT_ATTEMPTS:
            break
        sleep_s = min(2 ** attempt, 16) + random.uniform(0, 0.5)
        time.sleep(sleep_s)
    assert last_response is not None
    return last_response


def _truncate(value, limit: int = 400) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit]


def _resolve_mode(result_mode: str | None) -> str:
    mode = (result_mode or RESULT_MODE or "poll").strip().lower()
    if mode not in ("webhook", "poll"):
        raise ValueError(f"RUNPOD_RESULT_MODE must be 'webhook' or 'poll', got {mode!r}")
    return mode


def _webhook_url() -> str:
    return f"{PUBLIC_BASE_URL}/runpod/webhook/{WEBHOOK_SECRET}"


def _result_from_job_payload(
    data: dict,
    *,
    job_id: str,
    started: float,
    delivered_via: str,
    received_at: float | None = None,
):
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    notify_at = received_at if received_at is not None else time.perf_counter()
    notify_ms = int((notify_at - started) * 1000)
    status = (data or {}).get("status")

    if status != "COMPLETED":
        err_bits = (data or {}).get("error") or data
        return {
            "ok": False,
            "error": f"RunPod status={status}: {_truncate(err_bits)}",
            "elapsed_ms": elapsed_ms,
            "notify_ms": notify_ms,
            "job_id": job_id,
            "delivered_via": delivered_via,
        }

    try:
        parsed = extract_image((data or {}).get("output"))
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{exc} Raw: {_truncate(data)}",
            "elapsed_ms": elapsed_ms,
            "notify_ms": notify_ms,
            "job_id": job_id,
            "delivered_via": delivered_via,
        }

    delay_ms = (data or {}).get("delayTime")
    exec_ms = (data or {}).get("executionTime")
    extra_parts = [f"endpoint {ENDPOINT_ID}", f"via {delivered_via}"]
    if delay_ms is not None:
        extra_parts.append(f"delay {delay_ms} ms")
    if exec_ms is not None:
        extra_parts.append(f"exec {exec_ms} ms")
    if notify_ms is not None:
        extra_parts.append(f"notify {notify_ms} ms")

    return {
        "ok": True,
        "elapsed_ms": elapsed_ms,
        "notify_ms": notify_ms,
        "delay_ms": delay_ms,
        "exec_ms": exec_ms,
        "job_id": job_id,
        "delivered_via": delivered_via,
        "extra": " · ".join(extra_parts),
        **parsed,
    }


def _poll_until_terminal(job_id: str, headers: dict, started: float, deadline: float, data: dict):
    """Poll /status with 0.5s → ×1.5 → cap 2s. Never resubmits.

    Transient network / 5xx blips are retried until the overall deadline so a
    cold-start worker (60–90s+) is not abandoned early.
    """
    poll_sleep = 0.5
    while True:
        status = (data or {}).get("status")
        if status in TERMINAL_STATUSES:
            return data, None

        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return None, {
                "ok": False,
                "error": f"RunPod job {job_id} timed out (not resubmitted).",
                "elapsed_ms": elapsed_ms,
                "job_id": job_id,
                "delivered_via": "poll",
            }

        time.sleep(min(poll_sleep, max(0.0, remaining)))
        poll_sleep = min(poll_sleep * 1.5, 2.0)

        try:
            status_resp = _request_with_rate_limit(
                "GET",
                f"{BASE_URL}/status/{job_id}",
                headers=headers,
            )
        except requests.RequestException as exc:
            # Soft-fail: keep waiting through cold starts / brief API hiccups.
            if time.perf_counter() >= deadline:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return None, {
                    "ok": False,
                    "error": f"RunPod status poll failed for job {job_id}: {exc}",
                    "elapsed_ms": elapsed_ms,
                    "job_id": job_id,
                    "delivered_via": "poll",
                }
            continue

        try:
            data = status_resp.json()
        except ValueError:
            data = {}

        if status_resp.status_code >= 400:
            if (
                status_resp.status_code in TRANSIENT_STATUS_HTTP
                and time.perf_counter() < deadline
            ):
                continue
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return None, {
                "ok": False,
                "error": (
                    f"RunPod status HTTP {status_resp.status_code} for job {job_id}: "
                    f"{_truncate(data or status_resp.text)}"
                ),
                "elapsed_ms": elapsed_ms,
                "job_id": job_id,
                "delivered_via": "poll",
            }


def generate(
    prompt: str,
    size: str = "1024x1024",
    seed: int | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
    *,
    num_inference_steps: int = 4,
    guidance_scale: float = 0.0,
    result_mode: str | None = None,
):
    """
    Submit a RunPod /run job and wait via webhook and/or /status poll.

    Success:
      {"ok": True, "elapsed_ms", "notify_ms", "delay_ms", "exec_ms", "job_id",
       "delivered_via", "extra", "b64"|"image_url", "mime"}
    Failure:
      {"ok": False, "error", "elapsed_ms", "job_id", "delivered_via"?}
    """
    if not API_KEY:
        return {"ok": False, "error": "RUNPOD_API_KEY is missing from the environment."}

    try:
        mode = _resolve_mode(result_mode)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if mode == "webhook":
        missing = []
        if not PUBLIC_BASE_URL:
            missing.append("PUBLIC_BASE_URL (or ngrok-endpoint)")
        if not WEBHOOK_SECRET:
            missing.append("RUNPOD_WEBHOOK_SECRET")
        if missing:
            return {
                "ok": False,
                "error": (
                    "Webhook mode needs "
                    + " and ".join(missing)
                    + ". Set them in .env or switch RUNPOD_RESULT_MODE=poll."
                ),
            }

    body = {
        "input": {
            "prompt": prompt,
            "size": size,
            "num_inference_steps": int(num_inference_steps),
            "guidance_scale": float(guidance_scale),
        }
    }
    if seed is not None:
        body["input"]["seed"] = int(seed)
    if mode == "webhook":
        body["webhook"] = _webhook_url()

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    started = time.perf_counter()
    deadline = started + timeout

    try:
        submit = _request_with_rate_limit(
            "POST",
            f"{BASE_URL}/run",
            headers=headers,
            json=body,
        )
    except requests.RequestException as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "error": f"RunPod submit failed: {exc}",
            "elapsed_ms": elapsed_ms,
            "job_id": None,
        }

    try:
        submit_data = submit.json()
    except ValueError:
        submit_data = {}

    if submit.status_code >= 400:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "error": f"RunPod HTTP {submit.status_code}: {_truncate(submit_data or submit.text)}",
            "elapsed_ms": elapsed_ms,
            "job_id": (submit_data or {}).get("id"),
        }

    job_id = (submit_data or {}).get("id")
    if not job_id:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "error": f"RunPod /run returned no job id: {_truncate(submit_data)}",
            "elapsed_ms": elapsed_ms,
            "job_id": None,
        }

    data = submit_data or {}

    # Already terminal on submit response (rare).
    if (data or {}).get("status") in TERMINAL_STATUSES:
        return _result_from_job_payload(
            data,
            job_id=job_id,
            started=started,
            delivered_via="poll" if mode == "poll" else "webhook",
        )

    if mode == "webhook":
        wait_s = min(WEBHOOK_WAIT_S, max(0.0, deadline - time.perf_counter()))
        entry = runpod_results.STORE.wait(job_id, wait_s)
        if entry is not None:
            return _result_from_job_payload(
                entry["payload"],
                job_id=job_id,
                started=started,
                delivered_via="webhook",
                received_at=entry["received_at"],
            )
        # Safety-net: ngrok hiccup — poll remaining time, do not resubmit.
        data, err = _poll_until_terminal(job_id, headers, started, deadline, data)
        if err is not None:
            err["delivered_via"] = "poll_fallback"
            err["error"] = (
                f"Webhook missed for job {job_id}; poll fallback also failed: {err['error']}"
            )
            return err
        return _result_from_job_payload(
            data,
            job_id=job_id,
            started=started,
            delivered_via="poll_fallback",
        )

    data, err = _poll_until_terminal(job_id, headers, started, deadline, data)
    if err is not None:
        return err
    return _result_from_job_payload(
        data,
        job_id=job_id,
        started=started,
        delivered_via="poll",
    )


# Alias used by test_app.py
def call_runpod(prompt: str, size: str, seed: int | None, result_mode: str | None = None):
    return generate(prompt, size=size, seed=seed, result_mode=result_mode)


if __name__ == "__main__":
    import json
    import sys

    prompt = " ".join(sys.argv[1:]) or "a lighthouse on a granite cliff at sunrise, oil painting"
    print("endpoint:", ENDPOINT_ID)
    print("key set:", bool(API_KEY))
    print("result_mode:", RESULT_MODE)
    result = generate(prompt, size="1024x1024", seed=42)
    # Don't dump megabytes of base64 to the terminal.
    preview = dict(result)
    if preview.get("b64"):
        preview["b64"] = f"<{len(result['b64'])} chars>"
    print(json.dumps(preview, indent=2))
    if result.get("ok"):
        print(
            f"delay_ms={result.get('delay_ms')} exec_ms={result.get('exec_ms')} "
            f"notify_ms={result.get('notify_ms')} via={result.get('delivered_via')}"
        )
