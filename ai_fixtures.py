"""Deterministic AI / image fixtures used when MOCK_AI=true.

Production callers never import this unless the flag is set. When MOCK_AI is
unset, nothing here runs and behavior matches today.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

# 1×1 PNG — valid image bytes for worksheets and webhook payloads.
FIXTURE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_INTEREST_RE = re.compile(
    r"interested in (?P<interest>.+?)(?:\.| Focus vocabulary|$)",
    re.IGNORECASE | re.DOTALL,
)
_TOPIC_RE = re.compile(r"Write the story about:\s*(?P<topic>.+?)\.", re.IGNORECASE)


def mock_ai_enabled() -> bool:
    return (os.getenv("MOCK_AI") or "").strip().lower() in {"1", "true", "yes", "on"}


def assert_mock_ai_safe_to_start() -> None:
    """Refuse MOCK_AI in Railway / explicit production."""
    if not mock_ai_enabled():
        return
    railway = (os.getenv("RAILWAY_ENVIRONMENT") or "").strip()
    project = (os.getenv("RAILWAY_PROJECT_ID") or "").strip()
    flask_env = (os.getenv("FLASK_ENV") or "").strip().lower()
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    if railway or project or flask_env == "production" or app_env == "production":
        raise RuntimeError(
            "MOCK_AI=true is not allowed in a production environment "
            "(RAILWAY_ENVIRONMENT / RAILWAY_PROJECT_ID / FLASK_ENV / APP_ENV)."
        )


def _interest_from_prompt(prompt: str) -> str:
    text = prompt or ""
    match = _INTEREST_RE.search(text)
    if match:
        return " ".join(match.group("interest").split())[:80]
    match = _TOPIC_RE.search(text)
    if match:
        return " ".join(match.group("topic").split())[:80]
    return "learning"


def mock_worksheet_json(prompt: str, question_count: int = 5) -> dict[str, Any]:
    interest = _interest_from_prompt(prompt)
    story = (
        f"Maya liked {interest}. She read a short book about {interest} with her class. "
        f"Then she drew a picture of {interest} and told a friend one new fact. "
        f"At the end of the day, Maya still wanted to learn more about {interest}."
    )
    questions = {}
    stems = [
        f"What did Maya like learning about?",
        f"What did Maya read with her class?",
        f"What did Maya draw a picture of?",
        f"Who did Maya tell a new fact to?",
        f"What did Maya still want at the end of the day?",
        f"Where did Maya learn about {interest}?",
        f"What did Maya do after reading?",
        f"How did Maya feel about {interest}?",
    ]
    count = max(1, min(int(question_count or 5), 8))
    for index in range(count):
        questions[f"q{index + 1}"] = stems[index % len(stems)]
    return {
        "title": f"Maya and {interest.title()}",
        "story": story,
        "questions": questions,
    }


def mock_questions_json(prompt: str, question_count: int = 5) -> dict[str, Any]:
    interest = _interest_from_prompt(prompt) if "interested" in (prompt or "").lower() else "the story"
    count = max(1, min(int(question_count or 5), 8))
    questions = {}
    for index in range(count):
        questions[f"q{index + 1}"] = f"What is one detail from the story about {interest}?"
    return {"questions": questions}


def mock_generate_json(prompt: str, schema: dict, *, name: str = "response") -> dict:
    # Question-only payloads ask for a questions object without a story.
    props = (schema or {}).get("properties") or {}
    if name == "worksheet" or ("story" in props and "title" in props):
        # Prefer requested count from schema if present.
        q_schema = ((props.get("questions") or {}).get("properties")) or {}
        count = len(q_schema) or 5
        return mock_worksheet_json(prompt, count)
    if "questions" in props or name in {"questions", "worksheet_questions"}:
        q_schema = ((props.get("questions") or {}).get("properties")) or {}
        count = len(q_schema) or 5
        return mock_questions_json(prompt, count)
    # convert_question / classify fallbacks
    if "question" in props:
        return {"question": "What happened in the story?"}
    if "block_type" in props:
        return {"block_type": "passage"}
    return mock_worksheet_json(prompt, 5)


def mock_generate_text(prompt: str, system: str | None = None) -> str:
    """Rewrite / free-text mock: return the passage portion, lightly normalized."""
    del system
    text = prompt or ""
    marker = "Passage:\n"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    # Prefer the last long paragraph-looking chunk.
    parts = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 40]
    if parts:
        return parts[-1]
    return (
        "Maya liked learning. She read a short book with her class. "
        "Then she drew a picture and told a friend one new fact."
    )


def runpod_webhook_secret() -> str:
    return (os.getenv("RUNPOD_WEBHOOK_SECRET") or "").strip()


def sign_runpod_webhook(body: bytes, secret: str | None = None) -> str:
    key = (secret if secret is not None else runpod_webhook_secret()).encode("utf-8")
    digest = hmac.new(key, body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_runpod_signature(body: bytes, header: str | None, secret: str | None = None) -> bool:
    expected = sign_runpod_webhook(body, secret=secret)
    provided = (header or "").strip()
    return hmac.compare_digest(expected, provided)


def post_fake_runpod_webhook(
    *,
    job_id: str,
    image_b64: str = FIXTURE_PNG_B64,
    base_url: str | None = None,
) -> requests.Response | None:
    """POST a signed fake completion to the app's Runpod webhook route."""
    secret = runpod_webhook_secret()
    if not secret:
        logger.warning("MOCK_AI image webhook skipped: RUNPOD_WEBHOOK_SECRET is unset")
        return None
    root = (base_url or os.getenv("PUBLIC_BASE_URL") or os.getenv("E2E_BASE_URL") or "").rstrip("/")
    if not root:
        logger.warning("MOCK_AI image webhook skipped: no PUBLIC_BASE_URL / E2E_BASE_URL")
        return None
    import json

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
    url = f"{root}/webhooks/runpod"
    return requests.post(url, data=body, headers=headers, timeout=10)
