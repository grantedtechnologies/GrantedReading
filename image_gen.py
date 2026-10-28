"""Flux illustration helpers — kept outside reading_level on purpose."""

from __future__ import annotations

import base64
import logging
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

FLUX_API_KEY = os.getenv("FLUX_API_KEY", "")
FLUX_API_URL = os.getenv("FLUX_API_URL", "")
FLUX_MODEL = os.getenv("FLUX_MODEL", "")

_NO_TEXT_IN_IMAGE = (
    "The image must be a picture only. Do not paint, overlay, or caption any "
    "typed-out text anywhere in the image: no worksheet title, no story "
    "sentences, no action-beat lines, no captions, no labels, no speech "
    "bubbles with readable words, no watermarks, no signatures, and no "
    "letters or numbers of any kind. Use the story only as a description of "
    "what to draw, never as text to put in the picture."
)


def _classroom_grade_label(grade) -> str | None:
    if grade is None:
        return None
    raw = str(grade).strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered in ("k", "kg", "kindergarten"):
        return "kindergarten"
    match = re.match(r"^(\d+)", lowered)
    if not match:
        return raw
    number = int(match.group(1))
    if number == 1:
        ordinal = "1st"
    elif number == 2:
        ordinal = "2nd"
    elif number == 3:
        ordinal = "3rd"
    else:
        ordinal = f"{number}th"
    return f"{ordinal} grade"


def _typical_student_age(grade) -> str | None:
    if grade is None:
        return None
    raw = str(grade).strip().lower()
    if raw in ("k", "kg", "kindergarten"):
        return "5-6 year olds"
    match = re.match(r"^(\d+)", raw)
    if not match:
        return None
    number = int(match.group(1))
    younger = number + 5
    return f"{younger}-{younger + 1} year olds"


def create_image_prompt(title, story, feedback=None, grade=None):
    """Illustration brief. Characters age to classroom grade, not reading level."""
    del title  # callers pass it; never send it to Flux as drawable text
    scene = (story or "").strip()[:200]
    classroom = _classroom_grade_label(grade)
    ages = _typical_student_age(grade)
    if classroom and ages:
        prompt = (
            f"An educational illustration for a {classroom} classroom. "
            f"Characters should look like typical {classroom} "
            f"students ({ages}): age-appropriate faces, clothing, and setting. "
            f"Do not depict younger children unless the story requires it. "
            f"Scene to draw: {scene} {_NO_TEXT_IN_IMAGE}"
        )
    else:
        prompt = (
            f"An educational illustration. Scene to draw: {scene} "
            f"{_NO_TEXT_IN_IMAGE}"
        )
    if feedback:
        prompt += f" Additional instructions: {feedback.strip()}"
    return prompt


def generate_image(image_prompt):
    """Call Flux and return a base64 JPEG string."""
    # Lazy import keeps this file free of circular imports with llm.py.
    from llm import ensure_ai_configured, user_error_message

    ensure_ai_configured()
    url = f"{FLUX_API_URL.rstrip('/')}?api-version=preview"

    headers = {
        "Authorization": f"Bearer {FLUX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": FLUX_MODEL,
        "prompt": image_prompt,
        "width": 1024,
        "height": 1024,
        "output_format": "jpeg",
        "num_images": 1,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=None)
    except Exception as exc:
        raise RuntimeError(user_error_message(exc, "flux")) from exc

    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.status_code >= 400:
        err_payload = dict(data) if isinstance(data, dict) else {}
        err_payload.setdefault("status_code", response.status_code)
        raise RuntimeError(user_error_message(err_payload, "flux"))
    if not isinstance(data, dict) or "data" not in data:
        raise RuntimeError(
            "Azure image generation did not return a picture. Try generating again."
        )

    try:
        img = data["data"][0]
    except (IndexError, KeyError, TypeError):
        raise RuntimeError(
            "Azure image generation did not return a picture. Try generating again."
        )

    if "b64_json" in img:
        return img["b64_json"]

    if "url" in img:
        try:
            img_bytes = requests.get(img["url"], timeout=None).content
        except Exception as exc:
            raise RuntimeError(user_error_message(exc, "flux")) from exc
        return base64.b64encode(img_bytes).decode()

    raise RuntimeError(
        "Azure image generation did not return a picture. Try generating again."
    )
