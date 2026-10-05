"""Test board. A click starts that square's virtual users.

    python3 VUser/app.py

Then open http://127.0.0.1:8765/load
"""
import os
import threading

from flask import Flask, abort, jsonify, render_template, request

import tests

ROOT = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"))

PROFILES = ("load", "stress", "spike")
LEVELS = (("low", "Low"), ("med", "Medium"), ("high", "High"))
ROWS = (
    ("Generate sheets", "generate"),
    ("Update sheets", "update"),
    ("Email verification", "email"),
    ("DB connectivity", "db"),
)

NAMES = {kind: label for label, kind in ROWS}
INTRO = {
    "load": (
        "A load test holds a steady crowd the way a class period does. "
        "Generate, update, and database checks use 10 users on Low, then step "
        "from 25 to 50 on Medium and from 50 to 100 on High, each held for about "
        "10 minutes. Email stays at 2, 3, and 5 users. Each of those passes sends "
        "two real emails and opens a Stripe Checkout, so a hundred of them would "
        "flood the inbox and the billing page."
    ),
    "stress": (
        "A stress test keeps adding users past a normal class until something gives. "
        "Generate, update, and database checks climb to 100 on Low, 400 on Medium, "
        "and 600 on High, a few minutes at each step. Email only climbs from 2 to 3, "
        "3 to 5, and 5 to 8. Each pass still sends two real emails and opens a Stripe "
        "Checkout, so that count stays small on purpose."
    ),
    "spike": (
        "A spike test sits quiet, jumps in 15 seconds, holds the peak for 2 minutes, "
        "then drops back. That is the rush when a whole class hits an action at once. "
        "Generate, update, and database checks jump to 50, 200, and 400. Email jumps "
        "only to 2, 3, and 5, because each pass sends two real emails and opens a "
        "Stripe Checkout."
    ),
    "custom": (
        "A custom test holds however many users you pick for however many minutes "
        "you pick, then winds down for 15 seconds. The other pages keep email on a "
        "few users because each pass sends two real emails and opens a Stripe "
        "Checkout. Here you set the count yourself, up to 500 users and 120 minutes, "
        "so a large email run sends that many real messages."
    ),
}
ABOUT = {
    "generate": "Logs in and generates one worksheet.",
    "update": "Generates a worksheet, then rebuilds the PDF with the same picture.",
    "email": "Sends a verification email and opens a Pro checkout.",
    "db": "Opens history, account, students, and a class roster.",
}

# Durations match the old k6 stage lists. Email stays on a few users
# because each pass sends real mail and opens a Stripe Checkout.
RUNS = {
    "load-low-generate": ("generate", [("1m", 10), ("10m", 10), ("1m", 0)]),
    "load-med-generate": ("generate", [("2m", 25), ("10m", 25), ("2m", 50), ("10m", 50), ("2m", 0)]),
    "load-high-generate": ("generate", [("2m", 50), ("10m", 50), ("2m", 100), ("10m", 100), ("2m", 0)]),
    "load-low-update": ("update", [("1m", 10), ("10m", 10), ("1m", 0)]),
    "load-med-update": ("update", [("2m", 25), ("10m", 25), ("2m", 50), ("10m", 50), ("2m", 0)]),
    "load-high-update": ("update", [("2m", 50), ("10m", 50), ("2m", 100), ("10m", 100), ("2m", 0)]),
    "load-low-email": ("email", [("1m", 2), ("5m", 2), ("1m", 0)]),
    "load-med-email": ("email", [("1m", 3), ("10m", 3), ("1m", 0)]),
    "load-high-email": ("email", [("1m", 5), ("10m", 5), ("1m", 0)]),
    "load-low-db": ("db", [("1m", 10), ("10m", 10), ("1m", 0)]),
    "load-med-db": ("db", [("2m", 25), ("10m", 25), ("2m", 50), ("10m", 50), ("2m", 0)]),
    "load-high-db": ("db", [("2m", 50), ("10m", 50), ("2m", 100), ("10m", 100), ("2m", 0)]),
    "stress-low-generate": ("generate", [("2m", 25), ("3m", 50), ("3m", 100), ("2m", 0)]),
    "stress-med-generate": ("generate", [("2m", 50), ("3m", 100), ("3m", 200), ("3m", 300), ("3m", 400), ("3m", 0)]),
    "stress-high-generate": ("generate", [("2m", 100), ("3m", 200), ("3m", 400), ("3m", 600), ("3m", 0)]),
    "stress-low-update": ("update", [("2m", 25), ("3m", 50), ("3m", 100), ("2m", 0)]),
    "stress-med-update": ("update", [("2m", 50), ("3m", 100), ("3m", 200), ("3m", 300), ("3m", 400), ("3m", 0)]),
    "stress-high-update": ("update", [("2m", 100), ("3m", 200), ("3m", 400), ("3m", 600), ("3m", 0)]),
    "stress-low-email": ("email", [("1m", 2), ("3m", 3), ("1m", 0)]),
    "stress-med-email": ("email", [("1m", 3), ("3m", 5), ("1m", 0)]),
    "stress-high-email": ("email", [("1m", 5), ("3m", 8), ("1m", 0)]),
    "stress-low-db": ("db", [("2m", 25), ("3m", 50), ("3m", 100), ("2m", 0)]),
    "stress-med-db": ("db", [("2m", 50), ("3m", 100), ("3m", 200), ("3m", 300), ("3m", 400), ("3m", 0)]),
    "stress-high-db": ("db", [("2m", 100), ("3m", 200), ("3m", 400), ("3m", 600), ("2m", 0)]),
    "spike-low-generate": ("generate", [("1m", 5), ("15s", 50), ("2m", 50), ("15s", 5), ("3m", 5)]),
    "spike-med-generate": ("generate", [("1m", 10), ("15s", 200), ("2m", 200), ("15s", 10), ("3m", 10)]),
    "spike-high-generate": ("generate", [("1m", 10), ("15s", 400), ("2m", 400), ("15s", 10), ("3m", 10)]),
    "spike-low-update": ("update", [("1m", 5), ("15s", 50), ("2m", 50), ("15s", 5), ("3m", 5)]),
    "spike-med-update": ("update", [("1m", 10), ("15s", 200), ("2m", 200), ("15s", 10), ("3m", 10)]),
    "spike-high-update": ("update", [("1m", 10), ("15s", 400), ("2m", 400), ("15s", 10), ("3m", 10)]),
    "spike-low-email": ("email", [("1m", 1), ("15s", 2), ("1m", 2), ("15s", 1), ("2m", 1)]),
    "spike-med-email": ("email", [("1m", 1), ("15s", 3), ("2m", 3), ("15s", 1), ("3m", 1)]),
    "spike-high-email": ("email", [("1m", 2), ("15s", 5), ("2m", 5), ("15s", 2), ("3m", 2)]),
    "spike-low-db": ("db", [("1m", 5), ("15s", 50), ("2m", 50), ("15s", 5), ("3m", 5)]),
    "spike-med-db": ("db", [("1m", 10), ("15s", 200), ("2m", 200), ("15s", 10), ("3m", 10)]),
    "spike-high-db": ("db", [("1m", 10), ("15s", 400), ("2m", 400), ("15s", 10), ("3m", 10)]),
}

lock = threading.Lock()
state = {"running": None, "active": None}


def label(test_id):
    parts = test_id.split("-")
    name = NAMES.get(parts[-1], parts[-1])
    if parts[0] == "custom":
        return name
    return f"{name}, {parts[1]}"


def stage_seconds(stages):
    return int(round(sum(tests.seconds(duration) for duration, _target in stages)))


def square_meta(stages):
    total = stage_seconds(stages)
    minutes, secs = divmod(total, 60)
    if minutes and secs:
        span = f"{minutes} min {secs} sec"
    elif minutes:
        span = f"{minutes} min"
    else:
        span = f"{secs} sec"
    counts = [target for _duration, target in stages if target]
    peak = max(counts) if counts else 0
    if len(set(counts)) <= 1:
        users = f"{peak} users"
    else:
        users = f"up to {peak} users"
    return f"{users} · {span}"


def _execute(kind, stages):
    running = tests.Running(tests.SCENARIOS[kind])
    with lock:
        state["active"] = running
    try:
        tests.run_stages(stages, running, lambda: False)
        running.finish_waiting()
        total = running.tally.passed + running.tally.failed
        rate = running.tally.rate()
        pct = round(rate * 100, 1)
        ok = total > 0 and rate >= 0.99
        word = "Finished" if ok else "Failed"
        return {
            "ok": ok,
            "detail": f"{word} · {pct}% passed",
            "stats": running.tally.summary(),
        }
    except Exception as exc:
        running.stop.set()
        return {
            "ok": False,
            "detail": f"Failed · {exc}",
            "stats": running.tally.summary(),
        }
    finally:
        with lock:
            state["active"] = None


def _claim(test_id, kind, stages):
    with lock:
        if state["running"]:
            return 409, {"message": f"Already running {label(state['running'])}."}
        state["running"] = test_id
    try:
        return 200, _execute(kind, stages)
    finally:
        with lock:
            if state["running"] == test_id:
                state["running"] = None


def start(test_id):
    if test_id not in RUNS:
        return 404, {"message": "That square is not a test."}
    kind, stages = RUNS[test_id]
    return _claim(test_id, kind, stages)


def start_custom(kind, minutes, users):
    if kind not in NAMES:
        return 400, {"message": "Pick a test."}
    try:
        minutes = int(minutes)
        users = int(users)
    except (TypeError, ValueError):
        return 400, {"message": "Duration and users need to be whole numbers."}
    if not 1 <= minutes <= 120:
        return 400, {"message": "Duration must be 1 to 120 minutes."}
    if not 1 <= users <= 500:
        return 400, {"message": "Users must be 1 to 500."}
    stages = [(f"{minutes}m", users), ("15s", 0)]
    return _claim(f"custom-{kind}", kind, stages)


@app.route("/")
@app.route("/<profile>")
def board(profile="load"):
    profile = (profile or "load").lower()
    kinds = [
        {"id": kind, "label": row_label, "about": ABOUT[kind]}
        for row_label, kind in ROWS
    ]
    if profile == "custom":
        return render_template(
            "board.html",
            profile="custom",
            rows=[],
            kinds=kinds,
            intro=INTRO["custom"],
        )
    if profile not in PROFILES:
        abort(404)
    rows = []
    for row_label, kind in ROWS:
        rows.append({
            "label": row_label,
            "kind": kind,
            "about": ABOUT[kind],
            "cells": [
                {
                    "id": f"{profile}-{level}-{kind}",
                    "name": name,
                    "meta": square_meta(RUNS[f"{profile}-{level}-{kind}"][1]),
                    "seconds": stage_seconds(RUNS[f"{profile}-{level}-{kind}"][1]),
                }
                for level, name in LEVELS
            ],
        })
    return render_template(
        "board.html",
        profile=profile,
        rows=rows,
        kinds=kinds,
        intro=INTRO[profile],
    )


@app.route("/api/run", methods=["POST"])
def api_run():
    body = request.get_json(silent=True) or {}
    if body.get("kind"):
        status, payload = start_custom(body.get("kind"), body.get("minutes"), body.get("users"))
    else:
        status, payload = start(body.get("id") or "")
    return jsonify(payload), status


if __name__ == "__main__":
    print("Test board at http://127.0.0.1:8765/load")
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)
