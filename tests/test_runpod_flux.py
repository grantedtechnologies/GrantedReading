"""Unit tests for runpod_flux.extract_image (no live API calls)."""
import base64

import pytest

from runpod_flux import extract_image


# Minimal valid-looking PNG header as base64 (enough for mime sniffing).
_PNG_B64 = base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
).decode("ascii")


def test_extract_image_vllm_omni_data_list():
    payload = {
        "created": 1710000000,
        "data": [{"b64_json": _PNG_B64, "url": None, "revised_prompt": None}],
    }
    parsed = extract_image(payload)
    assert parsed["b64"] == _PNG_B64
    assert parsed["image_url"] is None
    assert parsed["mime"] == "png"


def test_extract_image_plain_string():
    parsed = extract_image(_PNG_B64)
    assert parsed["b64"] == _PNG_B64
    assert parsed["mime"] == "png"


def test_extract_image_images_list():
    payload = {"images": [_PNG_B64]}
    parsed = extract_image(payload)
    assert parsed["b64"] == _PNG_B64


def test_extract_image_missing_raises():
    with pytest.raises(RuntimeError, match="Could not find an image"):
        extract_image({"created": 1, "data": []})
