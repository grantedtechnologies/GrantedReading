"""Virtual-user checks against the Granted Reading app.

Each scenario is one pass a virtual user repeats. A run ramps the number of
users through stages, the same shape the old k6 profiles used.
"""
import math
import re
import threading
import time

import requests

BASE = "https://grantedreading-production-3e95.up.railway.app"
TEACHER = {"email": "luis.rivera@granted.local", "password": "password123"}
VERIFY_EMAIL = "k6.verify@granted.local"
GENERATE_BODY = {
    "grade": "6",
    "reading_level": "4",
    "interests": "Paintings",
    "focus_vocabulary": None,
    "focus_phonics": None,
    "dok_level": "1",
    "question_count": 5,
    "student_id": None,
}


class Tally:
    def __init__(self):
        self.lock = threading.Lock()
        self.passed = 0
        self.failed = 0
        self.iterations = 0
        self.requests = 0
        self.failed_requests = 0
        self.times = []

    def check(self, ok):
        with self.lock:
            if ok:
                self.passed += 1
            else:
                self.failed += 1

    def iteration(self):
        with self.lock:
            self.iterations += 1

    def observe(self, elapsed_ms, failed):
        with self.lock:
            self.requests += 1
            self.times.append(elapsed_ms)
            if failed:
                self.failed_requests += 1

    def rate(self):
        total = self.passed + self.failed
        if total == 0:
            return 0
        return self.passed / total

    def summary(self):
        with self.lock:
            samples = sorted(self.times)
            passed = self.passed
            failed = self.failed
            iterations = self.iterations
            requests_made = self.requests
            failed_requests = self.failed_requests

        def percentile(p):
            if not samples:
                return None
            rank = max(0, math.ceil(p / 100 * len(samples)) - 1)
            return round(samples[min(rank, len(samples) - 1)], 1)

        return {
            "iterations": iterations,
            "requests": requests_made,
            "failed_requests": failed_requests,
            "checks_passed": passed,
            "checks_total": passed + failed,
            "avg_ms": round(sum(samples) / len(samples), 1) if samples else None,
            "median_ms": percentile(50),
            "p95_ms": percentile(95),
            "min_ms": round(samples[0], 1) if samples else None,
            "max_ms": round(samples[-1], 1) if samples else None,
        }


class TimedSession(requests.Session):
    """Records how long each call took."""

    def __init__(self, tally):
        super().__init__()
        self.tally = tally

    def request(self, method, url, **kwargs):
        started = time.perf_counter()
        try:
            response = super().request(method, url, **kwargs)
        except requests.RequestException:
            self.tally.observe((time.perf_counter() - started) * 1000, True)
            raise
        failed = response.status_code >= 400
        self.tally.observe((time.perf_counter() - started) * 1000, failed)
        return response


def _json(response):
    try:
        return response.json()
    except ValueError:
        return {}


def _login(session, tally, timeout):
    try:
        response = session.post(f"{BASE}/login", json=TEACHER, timeout=timeout)
    except requests.RequestException:
        tally.check(False)
        return False
    ok = response.status_code == 200
    tally.check(ok)
    return ok


def generate(session, vu, tally):
    if _login(session, tally, 30):
        try:
            response = session.post(f"{BASE}/generate", json=GENERATE_BODY, timeout=180)
            tally.check(response.status_code == 200)
        except requests.RequestException:
            tally.check(False)
    time.sleep(1)


def update(session, vu, tally):
    if not _login(session, tally, 30):
        time.sleep(1)
        return
    try:
        generated = session.post(f"{BASE}/generate", json=GENERATE_BODY, timeout=180)
    except requests.RequestException:
        tally.check(False)
        time.sleep(1)
        return
    if generated.status_code != 200:
        tally.check(False)
        time.sleep(1)
        return
    tally.check(True)
    data = _json(generated)
    content = data.get("content") or {}
    worksheet = {
        "title": content.get("title"),
        "story": content.get("story"),
        "grade": content.get("grade"),
        "questions": content.get("questions"),
        "generation_id": data.get("generation_id"),
        "image_base64": data.get("image_base64"),
    }
    try:
        reused = session.post(f"{BASE}/update-pdf", json=worksheet, timeout=180)
        tally.check(reused.status_code == 200)
    except requests.RequestException:
        tally.check(False)
    time.sleep(1)


def history(session, vu, tally):
    if not _login(session, tally, 30):
        time.sleep(1)
        return
    try:
        page = session.get(f"{BASE}/worksheets/history", timeout=30, allow_redirects=False)
        tally.check(page.status_code == 200)
        account = session.get(f"{BASE}/account", timeout=30, allow_redirects=False)
        tally.check(account.status_code == 200)
        students = session.get(f"{BASE}/api/students", timeout=30)
        rows = (_json(students).get("students") or [])
        students_ok = students.status_code == 200 and len(rows) > 0
        tally.check(students_ok)
        if students_ok:
            class_id = rows[0].get("class_id")
            roster = session.get(f"{BASE}/api/classes/{class_id}/students", timeout=30)
            classroom = (_json(roster).get("class") or {})
            tally.check(
                roster.status_code == 200 and classroom.get("class_id") == class_id
            )
    except requests.RequestException:
        tally.check(False)
    time.sleep(1)


def email(session, vu, tally):
    signup_body = {
        "first_name": "K6",
        "last_name": "Verify",
        "email": VERIFY_EMAIL,
        "password": "Loadtest1!",
        "password_confirm": "Loadtest1!",
        "membership": "standard",
    }
    try:
        signup = session.post(f"{BASE}/signup", json=signup_body, timeout=30)
        body = _json(signup)
        tally.check(signup.status_code == 200 and body.get("email_sent") is True)
    except requests.RequestException:
        tally.check(False)
    try:
        resent = session.post(
            f"{BASE}/resend-verification",
            json={"email": VERIFY_EMAIL},
            timeout=30,
        )
        body = _json(resent)
        tally.check(
            resent.status_code == 200
            and body.get("email_sent") is True
            and body.get("needs_verification") is True
        )
    except requests.RequestException:
        tally.check(False)
    pro_email = f"k6.pro.{vu}.{time.time_ns()}@granted.local"
    try:
        checkout = session.post(
            f"{BASE}/signup",
            json={
                "first_name": "K6",
                "last_name": "Pro",
                "email": pro_email,
                "password": "Loadtest1!",
                "password_confirm": "Loadtest1!",
                "membership": "pro",
            },
            timeout=30,
        )
        url = _json(checkout).get("checkout_url")
        tally.check(
            checkout.status_code == 200
            and isinstance(url, str)
            and url.startswith("https://")
        )
    except requests.RequestException:
        tally.check(False)
    time.sleep(1)


def _saved_sheet_id(html, interest):
    needle = f'<div class="fact-value">{interest}</div>'
    at = html.find(needle)
    if at < 0:
        return ""
    before = html[max(0, at - 8000):at]
    found = ""
    for match in re.finditer(r'data-sheet-id="(\d+)"', before):
        found = match.group(1)
    return found


def prepare_download():
    """Generate one sheet, then every user downloads that same id."""
    session = requests.Session()
    login = session.post(f"{BASE}/login", json=TEACHER, timeout=30)
    if login.status_code != 200:
        raise RuntimeError("login failed before generate")
    interest = f"locktest-{int(time.time() * 1000)}"
    body = dict(GENERATE_BODY, interests=interest)
    generated = session.post(f"{BASE}/generate", json=body, timeout=180)
    if generated.status_code != 200:
        raise RuntimeError(f"generate failed with {generated.status_code}")
    generation_id = _json(generated).get("generation_id")
    if not generation_id:
        raise RuntimeError("generate did not return a generation_id")
    return {
        "generation_id": generation_id,
        "interest": interest,
        "start_at": time.time() + 15,
    }


def download(session, vu, tally, prepared):
    if not _login(session, tally, 30):
        return
    wait = prepared["start_at"] - time.time()
    if wait > 0:
        time.sleep(wait)
    try:
        downloaded = session.post(
            f"{BASE}/worksheets/download/{prepared['generation_id']}",
            timeout=60,
        )
        sheet_id = downloaded.headers.get("X-Worksheet-Id", "")
        content_type = downloaded.headers.get("Content-Type", "")
        tally.check(
            downloaded.status_code == 200
            and "pdf" in content_type
            and sheet_id != ""
        )
        history_page = session.get(
            f"{BASE}/worksheets/history",
            timeout=30,
            allow_redirects=False,
        )
        html = history_page.text or ""
        needle = f'<div class="fact-value">{prepared["interest"]}</div>'
        saved_rows = html.count(needle)
        history_sheet = _saved_sheet_id(html, prepared["interest"])
        tally.check(
            history_page.status_code == 200
            and saved_rows == 1
            and history_sheet == sheet_id
        )
    except requests.RequestException:
        tally.check(False)


SCENARIOS = {
    "generate": generate,
    "update": update,
    "email": email,
    "db": history,
    "download": download,
}


class Running:
    def __init__(self, scenario, prepared=None):
        self.scenario = scenario
        self.prepared = prepared
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.target = 0
        self.next_id = 0
        self.threads = []
        self.tally = Tally()

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

    def _loop(self, vu):
        session = TimedSession(self.tally)
        while not self.stop.is_set():
            with self.lock:
                if vu > self.target:
                    return
            try:
                self.tally.iteration()
                if self.prepared is None:
                    self.scenario(session, vu, self.tally)
                else:
                    self.scenario(session, vu, self.tally, self.prepared)
            except Exception:
                self.tally.check(False)

    def finish_waiting(self):
        self.stop.set()
        for worker in list(self.threads):
            worker.join()


def seconds(text):
    if text.endswith("ms"):
        return int(text[:-2]) / 1000
    if text.endswith("s"):
        return int(text[:-1])
    if text.endswith("m"):
        return int(text[:-1]) * 60
    raise ValueError(text)


def run_stages(stages, running, should_stop):
    previous = 0
    for duration, target in stages:
        span = seconds(duration)
        began = time.monotonic()
        while True:
            if should_stop():
                running.set_target(0)
                return
            elapsed = time.monotonic() - began
            if elapsed >= span:
                desired = target
            else:
                desired = int(round(previous + (target - previous) * (elapsed / span)))
            running.set_target(max(0, desired))
            if elapsed >= span:
                break
            time.sleep(0.25)
        previous = target
    running.set_target(0)
