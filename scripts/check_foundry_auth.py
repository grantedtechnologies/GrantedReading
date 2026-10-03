"""Confirm the Foundry service principal can reach the project.

Uses the same credential and client the app uses, then sends one short
responses call — the same API worksheets use. The agent endpoint has no
/models route, so listing models returns 404 even when auth is valid.

    PYTHONPATH=. ./venv/bin/python scripts/check_foundry_auth.py
"""
import sys

import ai


def main():
    _project, openai_client = ai._get_openai_client()
    response = openai_client.responses.create(input="Reply with the single word ok.")
    text = (response.output_text or "").strip()
    if not text:
        print("Foundry auth reached the agent, but the reply was empty.")
        return 1
    print(f"Foundry auth succeeded. Agent replied: {text[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
