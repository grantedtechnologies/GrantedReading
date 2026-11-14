"""Worksheet illustration helpers — kept outside reading_level on purpose.

Production image generation uses RunPod (FLUX.1-schnell via vLLM-Omni).
"""

from __future__ import annotations

import base64
import logging
import re

import requests
from dotenv import load_dotenv

import runpod_flux

load_dotenv()

logger = logging.getLogger(__name__)

# Keep this short and first in the prompt — FLUX CLIP truncates at 77 tokens,
# and longer "no text" rules were getting cut off (see RunPod worker warnings).
_NO_TEXT_IN_IMAGE = (
    "Picture only: no readable text, letters, numbers, captions, labels, "
    "or watermarks anywhere in the image."
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
    """Illustration brief. Characters age to classroom grade, not reading level.

    Constraints come first so CLIP's 77-token window keeps them.
    """
    del title  # callers pass it; never send it to Flux as drawable text
    scene = (story or "").strip()[:120]
    classroom = _classroom_grade_label(grade)
    ages = _typical_student_age(grade)
    if classroom and ages:
        prompt = (
            f"{_NO_TEXT_IN_IMAGE} "
            f"Educational {classroom} illustration; characters look like "
            f"typical {classroom} students ({ages}). Scene: {scene}"
        )
    else:
        prompt = f"{_NO_TEXT_IN_IMAGE} Educational illustration. Scene: {scene}"
    if feedback:
        prompt += f" Extra: {feedback.strip()[:80]}"
    return prompt


def image_data_url(image_base64: str, mime: str | None = None) -> str:
    """Build a data URL; sniff PNG vs JPEG when mime is omitted."""
    chosen = (mime or "").strip().lower()
    if chosen.startswith("image/"):
        return f"data:{chosen};base64,{image_base64}"
    if chosen in ("png", "jpeg", "jpg", "webp"):
        kind = "jpeg" if chosen == "jpg" else chosen
        return f"data:image/{kind};base64,{image_base64}"
    try:
        raw = base64.b64decode((image_base64 or "")[:64] + "===")
        if raw.startswith(b"\x89PNG"):
            return f"data:image/png;base64,{image_base64}"
    except Exception:
        pass
    return f"data:image/png;base64,{image_base64}"


def generate_image(image_prompt):
    """Call RunPod Flux and return a base64 image string (PNG)."""
    # Lazy import keeps this file free of circular imports with llm.py.
    from llm import ensure_ai_configured, user_error_message

    ensure_ai_configured()

    # Poll /status until done. Allow up to 10 minutes for cold GPU workers.
    try:
        result = runpod_flux.generate(
            image_prompt,
            size="1024x1024",
            timeout=runpod_flux.DEFAULT_TIMEOUT_S,
        )
    except Exception as exc:
        logger.exception("RunPod image request raised")
        raise RuntimeError(user_error_message(exc, "flux")) from exc

    if not result.get("ok"):
        logger.error(
            "RunPod image failed job_id=%s via=%s elapsed_ms=%s error=%s",
            result.get("job_id"),
            result.get("delivered_via"),
            result.get("elapsed_ms"),
            result.get("error"),
        )
        raise RuntimeError(user_error_message(result.get("error") or result, "flux"))

    if result.get("b64"):
        logger.info(
            "RunPod image ok job_id=%s via=%s exec_ms=%s delay_ms=%s",
            result.get("job_id"),
            result.get("delivered_via"),
            result.get("exec_ms"),
            result.get("delay_ms"),
        )
        return result["b64"]

    image_url = result.get("image_url")
    if image_url:
        try:
            img_bytes = requests.get(image_url, timeout=60).content
        except Exception as exc:
            logger.exception("RunPod image URL download failed")
            raise RuntimeError(user_error_message(exc, "flux")) from exc
        return base64.b64encode(img_bytes).decode()

    raise RuntimeError(
        "Picture generation did not return a picture. Try generating again."
    )
