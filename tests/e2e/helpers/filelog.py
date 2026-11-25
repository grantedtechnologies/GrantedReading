"""Append E2E run details to a text file (terminal is often too small)."""

from __future__ import annotations

import atexit
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parents[3]
LOG_DIR = ROOT / "e2e_logs"
LATEST = LOG_DIR / "latest.txt"

_lock = Lock()
_log_path: Path | None = None
_fp = None


def log_path() -> Path | None:
    return _log_path


def start_run(run_id: str | None = None) -> Path:
    """Create/overwrite this session's log file. Returns the path."""
    global _log_path, _fp
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"_{run_id}" if run_id else ""
    path = LOG_DIR / f"e2e_{stamp}{suffix}.txt"
    with _lock:
        if _fp is not None:
            try:
                _fp.close()
            except Exception:
                pass
        _log_path = path
        _fp = path.open("w", encoding="utf-8")
        # Stable pointer for “open the last run” (truncated each session).
        try:
            LATEST.write_text("", encoding="utf-8")
        except Exception:
            pass
        atexit.register(close)
    write("Granted Reading E2E log")
    write(f"Started (UTC): {stamp}")
    write(f"Run id: {run_id or '-'}")
    write(f"Log file: {path}")
    write(f"Latest:   {LATEST}")
    write("=" * 72)
    write("")
    return path


def write(*parts: str) -> None:
    """Append a line/block to the log and mirror to stdout."""
    text = " ".join(str(p) for p in parts) if len(parts) != 1 else str(parts[0])
    if not text.endswith("\n"):
        text = text + "\n"
    print(text, end="", flush=True)
    with _lock:
        if _fp is None:
            return
        _fp.write(text)
        _fp.flush()
        try:
            with LATEST.open("a", encoding="utf-8") as latest:
                latest.write(text)
        except Exception:
            pass


def write_block(title: str, *lines: str) -> None:
    width = 72
    block = [f"\n{'=' * width}", title, *[str(line) for line in lines], f"{'=' * width}\n"]
    write("\n".join(block))


def close() -> None:
    global _fp
    with _lock:
        if _fp is not None:
            try:
                _fp.write(f"\n{'=' * 72}\nEnded (UTC): {datetime.now(timezone.utc).isoformat()}\n")
                _fp.flush()
                _fp.close()
            except Exception:
                pass
            _fp = None
