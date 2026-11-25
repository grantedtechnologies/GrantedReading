"""Mailpit HTTP API helpers for capturing verification emails."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Any
from urllib.parse import urlparse

import requests

_LINK_RE = re.compile(r"https?://[^\s\"'<>]+")
_managed_proc: subprocess.Popen | None = None


def mailpit_api() -> str:
    return (os.getenv("MAILPIT_API") or "http://localhost:8025").rstrip("/")


def is_available(timeout_s: float = 1.5) -> bool:
    """True when Mailpit's HTTP API responds."""
    try:
        response = requests.get(f"{mailpit_api()}/api/v1/info", timeout=timeout_s)
        return response.ok
    except Exception:
        return False


def _smtp_listen() -> str:
    host = (os.getenv("SMTP_HOST") or "localhost").strip() or "localhost"
    port = (os.getenv("SMTP_PORT") or "1025").strip() or "1025"
    if host in ("localhost", "127.0.0.1"):
        host = "127.0.0.1"
    return f"{host}:{port}"


def _ui_listen() -> str:
    parsed = urlparse(mailpit_api())
    host = parsed.hostname or "127.0.0.1"
    if host == "localhost":
        host = "127.0.0.1"
    port = parsed.port or 8025
    return f"{host}:{port}"


def ensure_running(*, timeout_s: float = 8.0) -> bool:
    """Return True if Mailpit is reachable, starting a local process when needed.

    If the API is already up, do nothing. Otherwise, if a `mailpit` binary is on
    PATH, spawn one for this pytest session (SMTP 1025 / UI+API 8025 by default).
    """
    global _managed_proc
    if is_available():
        return True

    binary = shutil.which("mailpit")
    if not binary:
        return False

    if _managed_proc is not None and _managed_proc.poll() is None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if is_available(timeout_s=0.5):
                return True
            time.sleep(0.2)
        return False

    smtp = _smtp_listen()
    ui = _ui_listen()
    _managed_proc = subprocess.Popen(
        [binary, f"--smtp={smtp}", f"--listen={ui}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _managed_proc.poll() is not None:
            _managed_proc = None
            return False
        if is_available(timeout_s=0.5):
            return True
        time.sleep(0.2)
    return False


def stop_managed() -> None:
    """Stop a Mailpit process started by ensure_running (if any)."""
    global _managed_proc
    proc = _managed_proc
    _managed_proc = None
    if proc is None:
        return
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_for_email(to: str, *, timeout_s: float = 30.0, subject_contains: str | None = None) -> dict[str, Any]:
    """Poll Mailpit until a message addressed to `to` appears."""
    import time

    deadline = time.monotonic() + timeout_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            response = requests.get(
                f"{mailpit_api()}/api/v1/search",
                params={"query": f"to:{to}"},
                timeout=5,
            )
            response.raise_for_status()
            messages = response.json().get("messages") or []
            for message in messages:
                if subject_contains and subject_contains not in (message.get("Subject") or ""):
                    continue
                detail = requests.get(
                    f"{mailpit_api()}/api/v1/message/{message['ID']}",
                    timeout=5,
                )
                detail.raise_for_status()
                return detail.json()
        except Exception as exc:  # noqa: BLE001 — poll until timeout
            last_error = exc
        time.sleep(0.4)
    raise TimeoutError(f"No Mailpit email for {to} within {timeout_s}s ({last_error})")


def extract_link(message: dict[str, Any], path_contains: str = "/verify") -> str:
    bodies = [
        message.get("Text") or "",
        message.get("HTML") or "",
    ]
    for body in bodies:
        for match in _LINK_RE.finditer(body):
            link = match.group(0).rstrip(").,]")
            if path_contains in link:
                return link
    raise ValueError(f"No link containing {path_contains!r} in email")


def delete_messages_for(to_pattern: str = "e2e+") -> None:
    """Best-effort cleanup of Mailpit messages for this run."""
    try:
        response = requests.get(
            f"{mailpit_api()}/api/v1/search",
            params={"query": f"to:{to_pattern}"},
            timeout=5,
        )
        response.raise_for_status()
        ids = [m["ID"] for m in (response.json().get("messages") or [])]
        if not ids:
            return
        requests.delete(
            f"{mailpit_api()}/api/v1/messages",
            json={"Ids": ids},
            timeout=5,
        )
    except Exception:
        return
