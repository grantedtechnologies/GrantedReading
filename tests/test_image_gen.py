"""image_gen uses RunPod (not Azure Flux) for worksheet pictures."""
import base64

import pytest

import image_gen


_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24).decode("ascii")


def test_image_data_url_sniffs_png():
    assert image_gen.image_data_url(_PNG_B64).startswith("data:image/png;base64,")


def test_generate_image_uses_runpod_poll(monkeypatch):
    monkeypatch.setattr("llm.ensure_ai_configured", lambda: None)

    calls = {}

    def fake_generate(prompt, size="1024x1024", seed=None, timeout=300, **kwargs):
        calls["prompt"] = prompt
        calls["size"] = size
        calls["timeout"] = timeout
        return {
            "ok": True,
            "b64": _PNG_B64,
            "mime": "png",
            "delivered_via": "poll",
        }

    monkeypatch.setattr(image_gen.runpod_flux, "generate", fake_generate)
    out = image_gen.generate_image("draw a lighthouse")
    assert out == _PNG_B64
    assert calls["timeout"] == image_gen.runpod_flux.DEFAULT_TIMEOUT_S
    assert calls["prompt"] == "draw a lighthouse"


def test_create_image_prompt_puts_no_text_first_and_stays_compact():
    prompt = image_gen.create_image_prompt(
        "Unused Title With Words",
        "Maria loved to cook with her abuela. They would mix, stir, and bake "
        "together every Saturday morning in the sunny kitchen.",
        grade=4,
    )
    assert prompt.startswith("Picture only:")
    assert "Unused Title" not in prompt
    # Rough CLIP budget: keep the whole prompt from blowing past ~77 tokens.
    assert len(prompt.split()) < 70


def test_generate_image_raises_on_runpod_failure(monkeypatch):
    monkeypatch.setattr("llm.ensure_ai_configured", lambda: None)
    monkeypatch.setattr(
        image_gen.runpod_flux,
        "generate",
        lambda *args, **kwargs: {"ok": False, "error": "RunPod status=FAILED: boom"},
    )
    with pytest.raises(RuntimeError) as excinfo:
        image_gen.generate_image("draw a lighthouse")
    msg = str(excinfo.value).lower()
    assert "{" not in msg
    assert "picture" in msg or "image" in msg


def test_ensure_ai_configured_requires_runpod_not_azure_flux(monkeypatch):
    import llm

    monkeypatch.setenv("AZURE_TENANT_ID", "t")
    monkeypatch.setenv("AZURE_CLIENT_ID", "c")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "s")
    monkeypatch.setenv(
        "LLM_ENDPOINT",
        "https://example.services.ai.azure.com/api/projects/demo",
    )
    monkeypatch.setattr(llm, "LLM_AGENT_NAME", "agent")
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.delenv("runpod-api-key", raising=False)

    with pytest.raises(RuntimeError, match="not set up"):
        llm.ensure_ai_configured()

    monkeypatch.setenv("RUNPOD_API_KEY", "rp-key")
    llm.ensure_ai_configured()
