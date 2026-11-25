"""E2E session fixtures: env safety, Flask server, run_id, teardown, failure logging."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

from tests.e2e.helpers import filelog
from tests.e2e.helpers.report import CaseLog

ROOT = Path(__file__).resolve().parents[2]
ENV_TEST = ROOT / ".env.test"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _banner(title: str, *lines: str) -> None:
    filelog.write_block(title, *lines)


def pytest_configure(config):
    config.addinivalue_line("markers", "live: real Azure LLM + Runpod (skipped by default)")
    config.addinivalue_line(
        "markers", "stripe_cli: requires `stripe listen` forwarding webhooks"
    )
    # Always show skip reasons in the terminal summary (-rs).
    if getattr(config.option, "reportchars", None) is not None:
        chars = config.option.reportchars or ""
        if "s" not in chars.lower():
            config.option.reportchars = f"{chars}s"
    # Start a log file early so collection/setup issues are captured.
    if not filelog.log_path():
        filelog.start_run()


def pytest_sessionfinish(session, exitstatus):
    path = filelog.log_path()
    filelog.write_block(
        "E2E SESSION FINISHED",
        f"exit status: {exitstatus}",
        f"full log:    {path or filelog.LATEST}",
        f"shortcut:    {filelog.LATEST}",
    )
    filelog.close()


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    path = filelog.log_path() or filelog.LATEST
    terminalreporter.write_sep("=", "E2E log file")
    terminalreporter.write_line(f"Full run log: {path}")
    terminalreporter.write_line(f"Latest link:  {filelog.LATEST}")


def pytest_runtest_logstart(nodeid, location):
    filelog.write(f"\n>>> START  {nodeid}")


def pytest_runtest_logfinish(nodeid, location):
    filelog.write(f"<<< DONE   {nodeid}")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    case: CaseLog | None = getattr(item, "_e2e_case", None)

    if report.when == "setup" and report.skipped:
        reason = ""
        if isinstance(report.longrepr, tuple) and len(report.longrepr) >= 3:
            reason = str(report.longrepr[2])
        else:
            reason = str(report.longrepr)
        _banner(
            "E2E SKIPPED (setup)",
            f"Test:   {item.nodeid}",
            f"Reason: {reason}",
        )
        return

    if report.when == "setup" and report.failed:
        error = str(report.longrepr) if report.longrepr else "unknown setup failure"
        _banner(
            "E2E SETUP FAILURE",
            f"Test:  {item.nodeid}",
            f"Error: {error}",
        )
        return

    if report.when == "call" and report.skipped:
        reason = str(report.longrepr)
        _banner(
            "E2E SKIPPED",
            f"Test:   {item.nodeid}",
            f"Reason: {reason}",
        )
        return

    if report.when == "call" and report.failed:
        error = str(report.longrepr) if report.longrepr else "unknown failure"
        short = error.strip().splitlines()[-1] if error.strip() else error
        page = item.funcargs.get("page") if hasattr(item, "funcargs") else None
        page_url = ""
        try:
            if page is not None:
                page_url = page.url
        except Exception:
            page_url = ""

        if case is not None:
            filelog.write(case.format_block(error=short))
            if page_url:
                filelog.write(f"Page URL: {page_url}\n")
            # Full traceback lives in the file (terminal often truncates).
            filelog.write("---- full error ----\n")
            filelog.write(error)
            filelog.write("---- end error ----\n")
        else:
            lines = [
                f"Test:     {item.nodeid}",
                f"Error:    {short}",
            ]
            if page_url:
                lines.append(f"Page URL: {page_url}")
            lines.append("(Tip: inject the `case` fixture and call case.expect(...))")
            _banner("E2E FAILURE", *lines)
            filelog.write("---- full error ----\n")
            filelog.write(error)
            filelog.write("---- end error ----\n")
        return

    if report.when == "call" and report.passed:
        if case is not None and (case.expected is not None or case.actual is not None):
            filelog.write(
                f"    ok expected={case.expected!r} actual={case.actual!r}"
            )
        else:
            filelog.write("    PASSED")



@pytest.fixture
def case(request) -> CaseLog:
    """Structured log for this test — use case.expect / case.expect_contains."""
    log = CaseLog(test=request.node.nodeid)
    request.node._e2e_case = log
    return log


@pytest.fixture(scope="session")
def run_id() -> str:
    return uuid.uuid4().hex[:10]


@pytest.fixture(scope="session")
def e2e_env(run_id):
    if not ENV_TEST.exists():
        _banner(
            "E2E ABORT — missing .env.test",
            f"Expected file: {ENV_TEST}",
            "Fix: cp .env.test.example .env.test",
            "Then set LOCAL_DATABASE_* and STRIPE_SECRET_KEY=sk_test_...",
            "Also need: MySQL (granteddb or *test*), Redis, Mailpit (SMTP 1025 / API 8025).",
        )
        pytest.exit(
            f"Missing {ENV_TEST}. Copy .env.test.example → .env.test and fill values.",
            returncode=2,
        )

    load_dotenv(ENV_TEST, override=True)

    db_name = (
        os.getenv("LOCAL_DATABASE_NAME")
        or os.getenv("MYSQL_DATABASE")
        or ""
    ).lower()
    # Allow dedicated *test* DBs, or local granteddb (same as typical .env).
    if "test" not in db_name and db_name != "granteddb":
        pytest.exit(
            f"Refusing to run E2E: database name {db_name!r} must contain 'test' "
            "or be 'granteddb'.",
            returncode=2,
        )
    stripe_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if not stripe_key.startswith("sk_test_"):
        pytest.exit(
            "Refusing to run E2E: STRIPE_SECRET_KEY must start with sk_test_.",
            returncode=2,
        )

    # Point MOCK_AI webhook posts at the server we will start.
    os.environ.setdefault("MOCK_AI", "true")
    os.environ.setdefault("EMAIL_BACKEND", "smtp")
    os.environ.setdefault("SMTP_HOST", "localhost")
    os.environ.setdefault("SMTP_PORT", "1025")

    # db.engine is built at import time (often from .env before .env.test).
    # Rebuild so pytest fixtures and the Flask child share .env.test credentials.
    import db as app_db
    from sqlalchemy import create_engine, text

    app_db.engine.dispose()
    app_db.engine = create_engine(app_db._database_url(), pool_pre_ping=True)
    try:
        with app_db.engine.connect() as conn:
            connected = conn.execute(text("SELECT DATABASE()")).scalar()
            conn.execute(text("SELECT 1 FROM Teachers LIMIT 1"))
    except Exception as exc:
        _banner(
            "E2E ABORT — cannot reach MySQL",
            f"database: {db_name}",
            f"error:    {exc}",
            "Fix LOCAL_DATABASE_* / MYSQL_* in .env.test (password must match MySQL).",
        )
        pytest.exit(f"MySQL preflight failed for {db_name!r}: {exc}", returncode=2)

    filelog.write(f"run_id bound: {run_id}")

    _banner(
        "E2E SESSION",
        f"run_id:   {run_id}",
        f"database: {db_name} (connected as {connected})",
        f"MOCK_AI:  {os.getenv('MOCK_AI')}",
        f"email:    {os.getenv('EMAIL_BACKEND')} → {os.getenv('SMTP_HOST')}:{os.getenv('SMTP_PORT')}",
        f"log file: {filelog.log_path()}",
        f"latest:   {filelog.LATEST}",
        "Note: e2e+*@granted.test rows are created/deleted in this database.",
    )
    return {
        "run_id": run_id,
        "db_name": db_name,
        "stripe_key": stripe_key,
    }


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _ensure_playwright_chromium() -> None:
    """Fail fast if Chromium binaries are missing (filesystem check only).

    Do not call sync_playwright() here — pytest-playwright already owns an
    asyncio loop, and Sync API cannot nest inside it.
    """
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.exit(
            "pytest-playwright is not installed. "
            "Run: pip install -r requirements-dev.txt",
            returncode=2,
        )

    # Resolve the expected headless-shell path the same way Playwright does.
    browsers_path = Path(
        os.getenv("PLAYWRIGHT_BROWSERS_PATH")
        or (Path.home() / "Library/Caches/ms-playwright")
    )
    # Match any chromium_headless_shell-* / chrome-headless-shell binary.
    matches = list(browsers_path.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"))
    if not matches:
        # Older layout / full chromium package.
        matches = list(browsers_path.glob("chromium-*/chrome-*/Chromium"))
        matches += list(browsers_path.glob("chromium-*/chrome-mac*/Google Chrome for Testing"))
    if not matches or not any(path.is_file() for path in matches):
        _banner(
            "E2E ABORT — Playwright browsers missing",
            f"Looked under: {browsers_path}",
            "Fix (from the project venv):",
            "  playwright install chromium",
        )
        pytest.exit(
            "Playwright Chromium missing. Run: playwright install chromium",
            returncode=2,
        )


@pytest.fixture(scope="session")
def base_url(e2e_env):
    _ensure_playwright_chromium()

    existing = (os.getenv("BASE_URL") or "").rstrip("/")
    if existing:
        os.environ["PUBLIC_BASE_URL"] = existing
        os.environ["E2E_BASE_URL"] = existing
        _banner("E2E using existing BASE_URL", existing)
        yield existing
        return

    preferred = int(os.getenv("E2E_PORT") or "0")
    if preferred and _port_in_use(preferred):
        _banner(
            f"E2E_PORT {preferred} already in use — picking a free port",
            "Avoids hitting a leftover `python app.py` that loaded a different .env.",
        )
        port = _free_port()
    else:
        port = preferred or _free_port()

    url = f"http://127.0.0.1:{port}"
    os.environ["PUBLIC_BASE_URL"] = url
    os.environ["E2E_BASE_URL"] = url
    os.environ["PORT"] = str(port)

    env = os.environ.copy()
    # Force the child Flask process to load .env.test (override local .env).
    env["GRANTED_ENV_FILE"] = str(ENV_TEST)
    env["PORT"] = str(port)
    env["PUBLIC_BASE_URL"] = url
    env["E2E_BASE_URL"] = url
    env["MOCK_AI"] = "true"
    env["EMAIL_BACKEND"] = "smtp"
    env.setdefault(
        "RUNPOD_WEBHOOK_SECRET",
        os.getenv("RUNPOD_WEBHOOK_SECRET") or "e2e-runpod-webhook-secret",
    )
    # redis_client prefers REDIS_HOST over a localhost REDIS_URL — clear cloud
    # leftovers so login sessions use local Redis (db 15).
    env["REDIS_URL"] = os.getenv("REDIS_URL") or "redis://localhost:6379/15"
    for key in (
        "REDIS_HOST",
        "REDISHOST",
        "REDIS_USER",
        "REDISUSER",
        "REDIS_USERNAME",
        "REDIS_PASSWORD",
        "REDISPASSWORD",
    ):
        env[key] = ""


    proc = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 45
    ready = False
    import urllib.request

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            pytest.exit(f"Flask E2E server exited early:\n{out}", returncode=2)
        try:
            urllib.request.urlopen(f"{url}/login", timeout=1)
            ready = True
            break
        except Exception:
            time.sleep(0.4)
    if not ready:
        proc.terminate()
        pytest.exit("Flask E2E server did not become ready in time.", returncode=2)

    _banner(
        "E2E Flask server ready",
        url,
        f"GRANTED_ENV_FILE={env.get('GRANTED_ENV_FILE')}",
        f"RUNPOD_WEBHOOK_SECRET set={'yes' if env.get('RUNPOD_WEBHOOK_SECRET') else 'no'}",
    )
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def unique_email(run_id, request) -> str:
    suffix = uuid.uuid4().hex[:6]
    return f"e2e+{run_id}-{request.node.name[:20]}-{suffix}@granted.test"


@pytest.fixture
def tracked_teachers():
    """Collect teacher ids created during a test for teardown."""
    ids: list[int] = []
    yield ids
    from tests.e2e.helpers import db as e2e_db

    e2e_db.delete_teachers(ids)


@pytest.fixture(scope="session")
def mailpit_server(e2e_env):
    """Ensure Mailpit is up for the session; auto-start if the binary is installed."""
    from tests.e2e.helpers import mailpit

    started = mailpit.ensure_running()
    if started:
        _banner(
            "E2E Mailpit ready",
            f"API:  {mailpit.mailpit_api()}",
            f"SMTP: {os.getenv('SMTP_HOST', 'localhost')}:{os.getenv('SMTP_PORT', '1025')}",
        )
    yield started
    mailpit.stop_managed()


@pytest.fixture
def require_mailpit(mailpit_server):
    """Skip when Mailpit SMTP/API is not reachable (forgot/verify flows)."""
    from tests.e2e.helpers import mailpit

    if not mailpit_server:
        pytest.skip(
            "Mailpit not reachable at "
            f"{mailpit.mailpit_api()} — install/start Mailpit "
            "(brew install mailpit; mailpit) for SMTP 1025 / API 8025."
        )


@pytest.fixture
def require_stripe():
    """Skip when STRIPE_SECRET_KEY is a placeholder that cannot create Checkout."""
    from tests.e2e.helpers import stripe_helpers

    if not stripe_helpers.is_configured():
        pytest.skip(
            "STRIPE_SECRET_KEY in .env.test is a placeholder "
            "(set a real sk_test_… key to exercise Pro checkout)."
        )


@pytest.fixture(scope="session", autouse=True)
def _optional_sweeper(e2e_env):
    """Delete stale e2e+ users older than 24h at session start."""
    try:
        from scripts import sweep_e2e_data

        sweep_e2e_data.sweep(older_than_hours=24, dry_run=False)
    except Exception as exc:
        # Sweeper is best-effort; tests still run.
        print(f"(e2e sweeper skipped: {exc})", flush=True)
    yield


def login_as(page, base_url: str, email: str, password: str = "Passw0rd!") -> None:
    page.goto(f"{base_url}/login")
    page.get_by_test_id("tab-signin").click()
    page.get_by_test_id("auth-email").fill(email)
    page.get_by_test_id("auth-password").fill(password)
    page.get_by_test_id("auth-submit").click()
    try:
        page.wait_for_url(lambda url: "/login" not in url, timeout=20000)
    except Exception as exc:
        error = ""
        try:
            error = page.get_by_test_id("auth-error").inner_text().strip()
        except Exception:
            pass
        raise AssertionError(
            f"login_as failed for {email!r}\n"
            f"  url:   {page.url}\n"
            f"  error: {error or '(no auth-error text)'}\n"
            f"  cause: {exc}"
        ) from exc

