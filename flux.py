"""Standalone FLUX.1-schnell Flask API.

Copy this file (and your own requirements/Dockerfile) into a separate deploy
folder. Not wired into Granted Reading yet.

Env:
  HF_TOKEN or HUGGING_FACE_HUB_TOKEN  — Hugging Face access token
  PORT                                — listen port (default 8080)
"""

from __future__ import annotations

import base64
import io
import os

import torch
from diffusers import FluxPipeline
from flask import Flask, jsonify, request
from huggingface_hub import login

app = Flask(__name__)

_token = (os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN") or "").strip()
if _token:
    login(token=_token)

if torch.cuda.is_available():
    device = "cuda"
elif torch.backends.mps.is_available():
    device = "mps"
else:
    device = "cpu"

# Load once, at cold start, outside the request handler
pipe = FluxPipeline.from_pretrained(
    "black-forest-labs/FLUX.1-schnell",
    torch_dtype=torch.float16 if device != "cpu" else torch.float32,
).to(device)


def generate(user_prompt, system_prompt="You are a helpful assistant"):
    prompt = f"System: {system_prompt}\nUser: {user_prompt}"
    image = pipe(
        prompt,
        guidance_scale=0.0,  # schnell ignores guidance, so keep it at 0
        num_inference_steps=4,  # schnell is distilled for 1–4 steps
        max_sequence_length=256,  # schnell's limit
        height=512,
        width=512,
    ).images[0]
    return image


@app.get("/health")
def health():
    return jsonify({"ok": True, "device": device})


@app.post("/generate")
def generate_route():
    body = request.get_json(silent=True) or {}
    user_prompt = (body.get("prompt") or body.get("user_prompt") or "").strip()
    if not user_prompt:
        return jsonify({"error": "Send a prompt."}), 400
    system_prompt = (body.get("system_prompt") or "You are a helpful assistant").strip()

    image = generate(user_prompt, system_prompt=system_prompt)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=90)
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return jsonify({"data": [{"b64_json": b64}]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT") or "8080"))
