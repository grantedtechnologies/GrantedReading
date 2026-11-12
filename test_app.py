"""Side-by-side image test UI + virtual-user load test: Azure Flux vs RunPod.

  source venv/bin/activate
  python test_app.py

Open http://127.0.0.1:5055

Env (from .env):
  FLUX_API_KEY, FLUX_API_URL, FLUX_MODEL  — Azure / current Granted path
  RUNPOD_API_KEY / runpod-api-key         — RunPod Bearer token
  RUNPOD_ENDPOINT_ID                      — optional, defaults to fxql6eq3fp8df1
  RUNPOD_RESULT_MODE                      — webhook | poll (default poll)
  PUBLIC_BASE_URL / ngrok-endpoint        — public base URL for webhooks
  RUNPOD_WEBHOOK_SECRET                   — path token for /runpod/webhook/<token>
"""

from __future__ import annotations

import hmac
import math
import os
import threading
import time

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request

import runpod_flux
import runpod_results
from runpod_flux import call_runpod

load_dotenv(override=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

FLUX_API_KEY = (os.getenv("FLUX_API_KEY") or "").strip()
FLUX_API_URL = (os.getenv("FLUX_API_URL") or "").strip()
FLUX_MODEL = (os.getenv("FLUX_MODEL") or "").strip()
RUNPOD_API_KEY = runpod_flux.API_KEY
RUNPOD_ENDPOINT_ID = runpod_flux.ENDPOINT_ID
PUBLIC_BASE_URL = runpod_flux.PUBLIC_BASE_URL
RUNPOD_WEBHOOK_SECRET = runpod_flux.WEBHOOK_SECRET
RUNPOD_RESULT_MODE = runpod_flux.RESULT_MODE

LOAD_PROFILES = {
    "light": [("20s", 1), ("30s", 3), ("20s", 1), ("5s", 0)],
    "medium": [("20s", 2), ("40s", 5), ("20s", 2), ("5s", 0)],
    "heavy": [("20s", 3), ("45s", 8), ("20s", 3), ("5s", 0)],
}

_load_lock = threading.Lock()
_load_state = {
    "running": False,
    "profile": None,
    "targets": None,
    "result_mode": None,
    "prompt": None,
    "size": "1024x1024",
    "seed": 42,
    "stage_index": 0,
    "stage_target": 0,
    "active_users": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "azure": None,
    "runpod": None,
}


PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Flux compare — Azure vs RunPod</title>
<style>
  :root {
    --ink: #1a1a1a;
    --muted: #6b7280;
    --border: #e5e7eb;
    --bg: #f3f4f6;
    --white: #fff;
    --green: #3aaa35;
    --green-dark: #2d8a29;
    --font: Nunito, system-ui, sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: var(--font);
    background: var(--bg);
    color: var(--ink);
  }
  main {
    max-width: 1100px;
    margin: 0 auto;
    padding: 32px 20px 64px;
  }
  h1 { font-size: 28px; margin: 0 0 8px; }
  h2 { font-size: 18px; margin: 0 0 12px; }
  .lead { color: var(--muted); margin: 0 0 24px; line-height: 1.5; }
  .card {
    background: var(--white);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 20px;
  }
  label { display: block; font-weight: 700; font-size: 14px; margin-bottom: 6px; }
  textarea, input, select {
    width: 100%;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    font: inherit;
    margin-bottom: 12px;
  }
  textarea { min-height: 100px; resize: vertical; }
  .row {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 12px;
  }
  .row-4 { grid-template-columns: 1fr 1fr 1fr 1fr; }
  @media (max-width: 800px) {
    .row, .row-4 { grid-template-columns: 1fr; }
  }
  .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px; }
  button {
    border: none;
    border-radius: 8px;
    padding: 10px 16px;
    font: inherit;
    font-weight: 800;
    cursor: pointer;
    background: var(--green);
    color: #fff;
  }
  button:hover { background: var(--green-dark); }
  button.secondary { background: #374151; }
  button.danger { background: #b91c1c; }
  button:disabled { opacity: 0.6; cursor: wait; }
  .status { margin-top: 12px; font-size: 14px; color: var(--muted); white-space: pre-wrap; }
  .status.error { color: #b91c1c; }
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
  .panel h2 { margin: 0 0 8px; font-size: 18px; }
  .meta { font-size: 13px; color: var(--muted); margin-bottom: 10px; }
  .frame {
    background: #111;
    border-radius: 10px;
    min-height: 280px;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }
  .frame img { max-width: 100%; display: block; }
  .placeholder { color: #9ca3af; padding: 24px; text-align: center; }
  table.stats {
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
  }
  table.stats th, table.stats td {
    text-align: left;
    padding: 8px 6px;
    border-bottom: 1px solid var(--border);
  }
  table.stats th { color: var(--muted); font-weight: 700; }
  .hint { font-size: 13px; color: var(--muted); margin: 0 0 12px; line-height: 1.45; }
</style>
</head>
<body>
<main>
  <h1>Flux compare</h1>
  <p class="lead">Same prompt against Azure (current Granted path) and your RunPod endpoint. Single shots below, then a virtual-user ramp for durability.</p>

  <section class="card">
    <label for="prompt">Prompt</label>
    <textarea id="prompt">a lighthouse on a granite cliff at sunrise, oil painting</textarea>
    <div class="row">
      <div>
        <label for="size">Size</label>
        <select id="size">
          <option value="1024x1024" selected>1024x1024</option>
          <option value="768x768">768x768</option>
          <option value="512x512">512x512</option>
        </select>
      </div>
      <div>
        <label for="seed">Seed (RunPod)</label>
        <input id="seed" type="number" value="42" />
      </div>
      <div>
        <label for="targets">Target</label>
        <select id="targets">
          <option value="both" selected>Both</option>
          <option value="azure">Azure only</option>
          <option value="runpod">RunPod only</option>
        </select>
      </div>
    </div>
    <div class="actions">
      <button id="go" type="button">Generate once</button>
      <button id="classroom" class="secondary" type="button">Use classroom-style prompt</button>
    </div>
    <div class="status" id="status"></div>
  </section>

  <section class="grid">
    <article class="card panel">
      <h2>Azure (current app)</h2>
      <div class="meta" id="azure-meta">Not run yet</div>
      <div class="frame" id="azure-frame"><div class="placeholder">Waiting…</div></div>
    </article>
    <article class="card panel">
      <h2>RunPod</h2>
      <div class="meta" id="runpod-meta">Not run yet</div>
      <div class="frame" id="runpod-frame"><div class="placeholder">Waiting…</div></div>
    </article>
  </section>

  <section class="card">
    <h2>Virtual-user load</h2>
    <p class="hint">
      Ramps concurrent users through stages (same idea as the admin k6-style runner).
      With <b>Both</b>, half the users hit Azure and half hit RunPod at the same time so each side gets the same pressure.
    </p>
    <div class="row row-4">
      <div>
        <label for="profile">Profile</label>
        <select id="profile">
          <option value="light" selected>Light (1 → 3 → 1)</option>
          <option value="medium">Medium (2 → 5 → 2)</option>
          <option value="heavy">Heavy (3 → 8 → 3)</option>
        </select>
      </div>
      <div>
        <label for="result-mode">RunPod delivery</label>
        <select id="result-mode">
          <option value="poll">poll (/status)</option>
          <option value="webhook">webhook</option>
        </select>
      </div>
      <div>
        <label>Live users</label>
        <input id="live-users" value="0" disabled />
      </div>
      <div>
        <label>State</label>
        <input id="load-state" value="idle" disabled />
      </div>
    </div>
    <div class="actions">
      <button id="load-start" type="button">Start load test</button>
      <button id="load-stop" class="danger" type="button" disabled>Stop</button>
    </div>
    <div class="status" id="load-status"></div>
    <div class="grid" style="margin-top:16px">
      <div>
        <h2>Azure load</h2>
        <table class="stats" id="azure-stats"><tbody><tr><td>No data yet</td></tr></tbody></table>
      </div>
      <div>
        <h2>RunPod load</h2>
        <table class="stats" id="runpod-stats"><tbody><tr><td>No data yet</td></tr></tbody></table>
      </div>
    </div>
  </section>
</main>
<script>
  const classroomPrompt =
    "An educational illustration for a 4th grade classroom. Characters should look like typical 4th grade students (9-10 year olds): age-appropriate faces, clothing, and setting. Scene to draw: A child standing by a lighthouse on a rocky cliff at sunrise. The image must be a picture only. Do not paint any typed-out text, labels, captions, or watermarks.";

  document.getElementById("classroom").onclick = () => {
    document.getElementById("prompt").value = classroomPrompt;
  };

  function setStatus(message, isError) {
    const el = document.getElementById("status");
    el.textContent = message || "";
    el.className = "status" + (isError ? " error" : "");
  }

  function setLoadStatus(message, isError) {
    const el = document.getElementById("load-status");
    el.textContent = message || "";
    el.className = "status" + (isError ? " error" : "");
  }

  function showImage(frameId, metaId, payload) {
    const frame = document.getElementById(frameId);
    const meta = document.getElementById(metaId);
    if (!payload || !payload.ok) {
      meta.textContent = payload && payload.error ? payload.error : "Failed";
      frame.innerHTML = `<div class="placeholder">${(payload && payload.error) || "No image"}</div>`;
      return;
    }
    meta.textContent = `${payload.elapsed_ms} ms` +
      (payload.extra ? ` · ${payload.extra}` : "");
    const src = payload.image_url
      ? payload.image_url
      : `data:image/${payload.mime || "jpeg"};base64,${payload.b64}`;
    frame.innerHTML = `<img alt="result" src="${src}" />`;
  }

  function renderStats(tableId, summary, extras) {
    const table = document.getElementById(tableId);
    if (!summary) {
      table.innerHTML = "<tbody><tr><td>No data yet</td></tr></tbody>";
      return;
    }
    const rows = [
      ["Iterations", summary.iterations],
      ["Requests", summary.requests],
      ["Failed requests", summary.failed_requests],
      ["Checks passed", `${summary.checks_passed} / ${summary.checks_total}`],
      ["Pass rate", summary.pass_rate == null ? "—" : `${(summary.pass_rate * 100).toFixed(1)}%`],
      ["Avg ms", summary.avg_ms ?? "—"],
      ["Median ms", summary.median_ms ?? "—"],
      ["p95 ms", summary.p95_ms ?? "—"],
      ["Min ms", summary.min_ms ?? "—"],
      ["Max ms", summary.max_ms ?? "—"],
    ];
    if (extras) {
      if (extras.result_mode) rows.unshift(["Result mode", extras.result_mode]);
      if (summary.delivered_via) {
        const via = Object.entries(summary.delivered_via)
          .map(([k, v]) => `${k}: ${v}`)
          .join(", ") || "—";
        rows.push(["delivered_via", via]);
      }
      if (summary.avg_exec_ms != null) rows.push(["Avg exec_ms", summary.avg_exec_ms]);
      if (summary.avg_delay_ms != null) rows.push(["Avg delay_ms", summary.avg_delay_ms]);
      if (summary.avg_notify_ms != null) rows.push(["Avg notify_ms", summary.avg_notify_ms]);
    }
    table.innerHTML = "<tbody>" + rows.map(([k, v]) =>
      `<tr><th>${k}</th><td>${v}</td></tr>`
    ).join("") + "</tbody>";
  }

  async function loadConfig() {
    const response = await fetch("/api/config");
    const data = await response.json();
    const mode = data.runpod_result_mode || "poll";
    const select = document.getElementById("result-mode");
    if ([...select.options].some((o) => o.value === mode)) select.value = mode;
  }

  document.getElementById("go").onclick = async () => {
    const btn = document.getElementById("go");
    btn.disabled = true;
    setStatus("Generating…");
    try {
      const response = await fetch("/api/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: document.getElementById("prompt").value,
          size: document.getElementById("size").value,
          seed: Number(document.getElementById("seed").value),
          targets: document.getElementById("targets").value,
          result_mode: document.getElementById("result-mode").value,
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        setStatus(data.error || "Request failed", true);
        return;
      }
      showImage("azure-frame", "azure-meta", data.azure);
      showImage("runpod-frame", "runpod-meta", data.runpod);
      setStatus("Done.");
    } catch (err) {
      setStatus("Could not reach the test server.", true);
    } finally {
      btn.disabled = false;
    }
  };

  let pollTimer = null;

  async function refreshLoad() {
    const response = await fetch("/api/load/status");
    const data = await response.json();
    document.getElementById("live-users").value =
      `${data.active_users || 0} / target ${data.stage_target || 0}`;
    document.getElementById("load-state").value = data.running ? "running" : (data.finished_at ? "finished" : "idle");
    document.getElementById("load-start").disabled = !!data.running;
    document.getElementById("load-stop").disabled = !data.running;
    renderStats("azure-stats", data.azure);
    renderStats("runpod-stats", data.runpod, { result_mode: data.result_mode });
    if (data.error) setLoadStatus(data.error, true);
    else if (data.running) {
      setLoadStatus(
        `Running ${data.profile} against ${data.targets}` +
        (data.result_mode ? ` · RunPod ${data.result_mode}` : "") +
        "…"
      );
    }
    else if (data.finished_at) setLoadStatus("Load test finished.");
    if (!data.running && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  document.getElementById("load-start").onclick = async () => {
    setLoadStatus("Starting…");
    const response = await fetch("/api/load/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: document.getElementById("prompt").value,
        size: document.getElementById("size").value,
        seed: Number(document.getElementById("seed").value),
        targets: document.getElementById("targets").value,
        profile: document.getElementById("profile").value,
        result_mode: document.getElementById("result-mode").value,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      setLoadStatus(data.error || "Could not start", true);
      return;
    }
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(refreshLoad, 1000);
    refreshLoad();
  };

  document.getElementById("load-stop").onclick = async () => {
    await fetch("/api/load/stop", { method: "POST" });
    refreshLoad();
  };

  loadConfig();
  refreshLoad();
</script>
</body>
</html>
"""


class Tally:
    def __init__(self):
        self.lock = threading.Lock()
        self.passed = 0
        self.failed = 0
        self.iterations = 0
        self.requests = 0
        self.failed_requests = 0
        self.times = []
        self.delivered_via = {}
        self.exec_samples = []
        self.delay_samples = []
        self.notify_samples = []

    def check(self, ok):
        with self.lock:
            if ok:
                self.passed += 1
            else:
                self.failed += 1

    def iteration(self):
        with self.lock:
            self.iterations += 1

    def observe(self, elapsed_ms, failed, meta=None):
        with self.lock:
            self.requests += 1
            self.times.append(elapsed_ms)
            if failed:
                self.failed_requests += 1
            if meta:
                via = meta.get("delivered_via")
                if via:
                    self.delivered_via[via] = self.delivered_via.get(via, 0) + 1
                for key, bucket in (
                    ("exec_ms", self.exec_samples),
                    ("delay_ms", self.delay_samples),
                    ("notify_ms", self.notify_samples),
                ):
                    value = meta.get(key)
                    if value is not None:
                        try:
                            bucket.append(float(value))
                        except (TypeError, ValueError):
                            pass

    def summary(self):
        with self.lock:
            samples = sorted(self.times)
            passed = self.passed
            failed = self.failed
            iterations = self.iterations
            requests_made = self.requests
            failed_requests = self.failed_requests
            delivered_via = dict(self.delivered_via)
            exec_samples = list(self.exec_samples)
            delay_samples = list(self.delay_samples)
            notify_samples = list(self.notify_samples)

        def percentile(p):
            if not samples:
                return None
            rank = max(0, math.ceil(p / 100 * len(samples)) - 1)
            return round(samples[min(rank, len(samples) - 1)], 1)

        def avg(values):
            return round(sum(values) / len(values), 1) if values else None

        total_checks = passed + failed
        return {
            "iterations": iterations,
            "requests": requests_made,
            "failed_requests": failed_requests,
            "checks_passed": passed,
            "checks_total": total_checks,
            "pass_rate": (passed / total_checks) if total_checks else None,
            "avg_ms": round(sum(samples) / len(samples), 1) if samples else None,
            "median_ms": percentile(50),
            "p95_ms": percentile(95),
            "min_ms": round(samples[0], 1) if samples else None,
            "max_ms": round(samples[-1], 1) if samples else None,
            "delivered_via": delivered_via,
            "avg_exec_ms": avg(exec_samples),
            "avg_delay_ms": avg(delay_samples),
            "avg_notify_ms": avg(notify_samples),
        }


class Running:
    """Ramp concurrent virtual users. Each user loops one provider."""

    def __init__(self, provider, prompt, size, seed, tally, result_mode=None):
        self.provider = provider
        self.prompt = prompt
        self.size = size
        self.seed = seed
        self.tally = tally
        self.result_mode = result_mode
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.target = 0
        self.next_id = 0
        self.threads = []

    def set_target(self, count):
        with self.lock:
            self.target = count
            self.threads = [item for item in self.threads if item.is_alive()]
            while len(self.threads) < count:
                self.next_id += 1
                worker = threading.Thread(
                    target=self._loop,
                    args=(self.next_id,),
                    daemon=True,
                )
                worker.start()
                self.threads.append(worker)

    def active_users(self):
        with self.lock:
            self.threads = [item for item in self.threads if item.is_alive()]
            return min(len(self.threads), self.target)

    def _loop(self, vu):
        width, height = _parse_size(self.size)
        while not self.stop.is_set():
            with self.lock:
                if vu > self.target:
                    return
            self.tally.iteration()
            try:
                if self.provider == "azure":
                    result = call_azure(self.prompt, width, height)
                else:
                    # Vary seed slightly per VU so RunPod is not identical every time.
                    seed = None if self.seed is None else int(self.seed) + vu
                    result = call_runpod(
                        self.prompt, self.size, seed, result_mode=self.result_mode
                    )
                ok = bool(result.get("ok"))
                self.tally.check(ok)
                self.tally.observe(
                    float(result.get("elapsed_ms") or 0),
                    not ok,
                    meta=result if self.provider == "runpod" else None,
                )
            except Exception:
                self.tally.check(False)
                self.tally.observe(0, True)
            time.sleep(0.25)

    def finish_waiting(self):
        self.stop.set()
        self.set_target(0)
        for worker in list(self.threads):
            worker.join(timeout=2)


def seconds(text):
    if text.endswith("ms"):
        return int(text[:-2]) / 1000
    if text.endswith("s"):
        return int(text[:-1])
    if text.endswith("m"):
        return int(text[:-1]) * 60
    raise ValueError(text)


def run_stages(stages, runners, should_stop, on_tick):
    previous = 0
    for index, (duration, target) in enumerate(stages):
        span = seconds(duration)
        began = time.monotonic()
        while True:
            if should_stop():
                for runner in runners:
                    runner.set_target(0)
                on_tick(index, 0)
                return
            elapsed = time.monotonic() - began
            if elapsed >= span:
                desired = target
            else:
                desired = int(round(previous + (target - previous) * (elapsed / span)))
            desired = max(0, desired)
            if len(runners) == 2:
                # Split concurrent users evenly across Azure and RunPod.
                left = desired // 2
                right = desired - left
                runners[0].set_target(left)
                runners[1].set_target(right)
            else:
                runners[0].set_target(desired)
            on_tick(index, desired)
            if elapsed >= span:
                break
            time.sleep(0.25)
        previous = target
    for runner in runners:
        runner.set_target(0)
    on_tick(len(stages) - 1, 0)


def _parse_size(size: str):
    raw = (size or "1024x1024").lower().replace(" ", "")
    if "x" not in raw:
        return 1024, 1024
    left, right = raw.split("x", 1)
    return max(256, int(left)), max(256, int(right))


def call_azure(prompt: str, width: int, height: int):
    if not FLUX_API_KEY or not FLUX_API_URL or not FLUX_MODEL:
        return {"ok": False, "error": "Azure FLUX_* env vars are missing."}
    url = f"{FLUX_API_URL.rstrip('/')}?api-version=preview"
    started = time.perf_counter()
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {FLUX_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": FLUX_MODEL,
            "prompt": prompt,
            "width": width,
            "height": height,
            "output_format": "jpeg",
            "num_images": 1,
        },
        timeout=300,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        return {
            "ok": False,
            "error": f"Azure HTTP {response.status_code}: {data or response.text[:300]}",
            "elapsed_ms": elapsed_ms,
        }
    try:
        img = data["data"][0]
    except (KeyError, IndexError, TypeError):
        return {
            "ok": False,
            "error": f"Azure response missing image data: {str(data)[:300]}",
            "elapsed_ms": elapsed_ms,
        }
    if "b64_json" in img:
        return {
            "ok": True,
            "b64": img["b64_json"],
            "mime": "jpeg",
            "elapsed_ms": elapsed_ms,
            "extra": FLUX_MODEL,
        }
    if "url" in img:
        return {
            "ok": True,
            "image_url": img["url"],
            "elapsed_ms": elapsed_ms,
            "extra": FLUX_MODEL,
        }
    return {
        "ok": False,
        "error": "Azure returned neither b64_json nor url.",
        "elapsed_ms": elapsed_ms,
    }


def _webhook_config_error(result_mode: str | None = None):
    mode = (result_mode or RUNPOD_RESULT_MODE or "poll").strip().lower()
    if mode != "webhook":
        return None
    missing = []
    if not PUBLIC_BASE_URL:
        missing.append("PUBLIC_BASE_URL (or ngrok-endpoint)")
    if not RUNPOD_WEBHOOK_SECRET:
        missing.append("RUNPOD_WEBHOOK_SECRET")
    if not missing:
        return None
    return (
        "Webhook mode needs "
        + " and ".join(missing)
        + ". Set them in .env or choose poll mode."
    )


def _load_snapshot():
    with _load_lock:
        state = dict(_load_state)
        azure_tally = state.get("_azure_tally")
        runpod_tally = state.get("_runpod_tally")
        runners = state.get("_runners") or []
    active = sum(runner.active_users() for runner in runners) if runners else state.get("active_users", 0)
    return {
        "running": state["running"],
        "profile": state["profile"],
        "targets": state["targets"],
        "result_mode": state.get("result_mode"),
        "prompt": state["prompt"],
        "size": state["size"],
        "seed": state["seed"],
        "stage_index": state["stage_index"],
        "stage_target": state["stage_target"],
        "active_users": active,
        "started_at": state["started_at"],
        "finished_at": state["finished_at"],
        "error": state["error"],
        "azure": azure_tally.summary() if azure_tally else None,
        "runpod": runpod_tally.summary() if runpod_tally else None,
    }


def _run_load(profile, targets, prompt, size, seed, result_mode):
    stages = LOAD_PROFILES[profile]
    azure_tally = Tally()
    runpod_tally = Tally()
    runners = []
    if targets in ("both", "azure"):
        runners.append(Running("azure", prompt, size, seed, azure_tally))
    if targets in ("both", "runpod"):
        runners.append(
            Running("runpod", prompt, size, seed, runpod_tally, result_mode=result_mode)
        )

    with _load_lock:
        _load_state.update({
            "running": True,
            "profile": profile,
            "targets": targets,
            "result_mode": result_mode,
            "prompt": prompt,
            "size": size,
            "seed": seed,
            "stage_index": 0,
            "stage_target": 0,
            "active_users": 0,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "_azure_tally": azure_tally,
            "_runpod_tally": runpod_tally,
            "_runners": runners,
            "_stop": False,
        })

    def should_stop():
        with _load_lock:
            return bool(_load_state.get("_stop"))

    def on_tick(stage_index, target):
        with _load_lock:
            _load_state["stage_index"] = stage_index
            _load_state["stage_target"] = target

    try:
        run_stages(stages, runners, should_stop, on_tick)
    except Exception as exc:
        with _load_lock:
            _load_state["error"] = str(exc)
    finally:
        for runner in runners:
            runner.finish_waiting()
        with _load_lock:
            _load_state["running"] = False
            _load_state["finished_at"] = time.time()
            _load_state["stage_target"] = 0
            _load_state["active_users"] = 0


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.get("/api/config")
def config():
    return jsonify({
        "azure_configured": bool(FLUX_API_KEY and FLUX_API_URL and FLUX_MODEL),
        "runpod_configured": bool(RUNPOD_API_KEY),
        "runpod_endpoint_id": RUNPOD_ENDPOINT_ID,
        "runpod_result_mode": RUNPOD_RESULT_MODE,
        "public_base_url_set": bool(PUBLIC_BASE_URL),
        "webhook_secret_set": bool(RUNPOD_WEBHOOK_SECRET),
        "flux_model": FLUX_MODEL,
        "profiles": LOAD_PROFILES,
    })


@app.post("/runpod/webhook/<token>")
def runpod_webhook(token: str):
    secret = RUNPOD_WEBHOOK_SECRET or ""
    if not secret or not hmac.compare_digest(token, secret):
        return ("", 404)

    received_at = time.perf_counter()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected JSON object."}), 400
    job_id = payload.get("id")
    status = payload.get("status")
    if not job_id or not status:
        return jsonify({"error": "Missing id or status."}), 400

    payload_bytes = request.content_length if request.content_length is not None else 0
    app.logger.info(
        "runpod webhook job_id=%s status=%s bytes=%s",
        job_id,
        status,
        payload_bytes,
    )
    runpod_results.STORE.put(str(job_id), payload, received_at)
    return jsonify({"ok": True}), 200


@app.post("/api/compare")
def compare():
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Enter a prompt."}), 400
    size = (body.get("size") or "1024x1024").strip()
    width, height = _parse_size(size)
    seed = body.get("seed")
    try:
        seed = int(seed) if seed is not None and str(seed).strip() != "" else None
    except (TypeError, ValueError):
        return jsonify({"error": "Seed must be a number."}), 400
    targets = (body.get("targets") or "both").strip().lower()
    result_mode = (body.get("result_mode") or RUNPOD_RESULT_MODE or "poll").strip().lower()
    if result_mode not in ("webhook", "poll"):
        return jsonify({"error": "result_mode must be webhook or poll."}), 400
    if targets in ("both", "runpod"):
        webhook_err = _webhook_config_error(result_mode)
        if webhook_err:
            return jsonify({"error": webhook_err}), 400

    azure = {"ok": False, "error": "Skipped"}
    runpod = {"ok": False, "error": "Skipped"}
    if targets in ("both", "azure"):
        try:
            azure = call_azure(prompt, width, height)
        except Exception as exc:
            azure = {"ok": False, "error": str(exc)}
    if targets in ("both", "runpod"):
        try:
            runpod = call_runpod(prompt, size, seed, result_mode=result_mode)
        except Exception as exc:
            runpod = {"ok": False, "error": str(exc)}

    return jsonify({"azure": azure, "runpod": runpod})


@app.get("/api/load/status")
def load_status():
    return jsonify(_load_snapshot())


@app.post("/api/load/start")
def load_start():
    with _load_lock:
        if _load_state["running"]:
            return jsonify({"error": "A load test is already running."}), 409
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Enter a prompt."}), 400
    size = (body.get("size") or "1024x1024").strip()
    seed = body.get("seed")
    try:
        seed = int(seed) if seed is not None and str(seed).strip() != "" else None
    except (TypeError, ValueError):
        return jsonify({"error": "Seed must be a number."}), 400
    targets = (body.get("targets") or "both").strip().lower()
    if targets not in ("both", "azure", "runpod"):
        return jsonify({"error": "targets must be both, azure, or runpod."}), 400
    profile = (body.get("profile") or "light").strip().lower()
    if profile not in LOAD_PROFILES:
        return jsonify({"error": f"Unknown profile. Choose: {', '.join(LOAD_PROFILES)}"}), 400
    result_mode = (body.get("result_mode") or RUNPOD_RESULT_MODE or "poll").strip().lower()
    if result_mode not in ("webhook", "poll"):
        return jsonify({"error": "result_mode must be webhook or poll."}), 400
    if targets in ("both", "azure") and not (FLUX_API_KEY and FLUX_API_URL and FLUX_MODEL):
        return jsonify({"error": "Azure FLUX_* env vars are missing."}), 400
    if targets in ("both", "runpod") and not RUNPOD_API_KEY:
        return jsonify({"error": "runpod-api-key is missing from .env."}), 400
    if targets in ("both", "runpod"):
        webhook_err = _webhook_config_error(result_mode)
        if webhook_err:
            return jsonify({"error": webhook_err}), 400

    worker = threading.Thread(
        target=_run_load,
        args=(profile, targets, prompt, size, seed, result_mode),
        daemon=True,
    )
    worker.start()
    return jsonify({
        "ok": True,
        "profile": profile,
        "targets": targets,
        "result_mode": result_mode,
    })


@app.post("/api/load/stop")
def load_stop():
    with _load_lock:
        _load_state["_stop"] = True
        runners = list(_load_state.get("_runners") or [])
    for runner in runners:
        runner.set_target(0)
        runner.stop.set()
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("Azure configured:", bool(FLUX_API_KEY and FLUX_API_URL and FLUX_MODEL))
    print("RunPod configured:", bool(RUNPOD_API_KEY), "endpoint:", RUNPOD_ENDPOINT_ID)
    print("RunPod result mode:", RUNPOD_RESULT_MODE)
    print("Public base URL set:", bool(PUBLIC_BASE_URL))
    print("Webhook secret set:", bool(RUNPOD_WEBHOOK_SECRET))
    # threaded=True so /runpod/webhook can land while generate() waits on the store.
    app.run(
        host="127.0.0.1",
        port=int(os.getenv("TEST_APP_PORT") or "5055"),
        debug=False,
        threaded=True,
    )
