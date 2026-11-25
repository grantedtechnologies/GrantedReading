"""Local Ollama judge for live-tier generation checks.

Prompts are intentionally plain so they are easy to edit.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

DOK_PROMPT = """You are grading reading-comprehension questions.
Classify the Depth of Knowledge (DOK) level of EACH question as 1, 2, 3, or 4.
Return ONLY valid JSON:
{"levels":[1,2,...]}
with one integer per question, in order.

Questions:
{questions}
"""

TOPIC_PROMPT = """Does this reading passage clearly relate to the student's interest?
Interest: {interest}
Passage:
{passage}

Return ONLY valid JSON: {{"about_interest": true}} or {{"about_interest": false}}
"""


def ollama_url() -> str:
    return (os.getenv("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")


def judge_model() -> str:
    return (os.getenv("JUDGE_MODEL") or "llama3.2").strip()


def _chat(prompt: str) -> dict[str, Any]:
    response = requests.post(
        f"{ollama_url()}/api/chat",
        json={
            "model": judge_model(),
            "stream": False,
            "options": {"temperature": 0},
            "messages": [{"role": "user", "content": prompt}],
            "format": "json",
        },
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()["message"]["content"]
    return json.loads(content)


def classify_dok(questions: list[str]) -> list[int]:
    numbered = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    data = _chat(DOK_PROMPT.format(questions=numbered))
    levels = data.get("levels") or []
    return [int(level) for level in levels]


def passage_about_interest(passage: str, interest: str) -> bool:
    data = _chat(TOPIC_PROMPT.format(passage=passage, interest=interest))
    return bool(data.get("about_interest"))


def dok_match_rate(predicted: list[int], expected: int) -> float:
    if not predicted:
        return 0.0
    hits = sum(1 for level in predicted if int(level) == int(expected))
    return hits / len(predicted)
