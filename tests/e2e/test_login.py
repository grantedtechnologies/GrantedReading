"""Login / logout / forgot-password / auth gate through the browser."""

from __future__ import annotations

import pytest

from tests.e2e.helpers import mailpit
from tests.e2e.helpers.ui import wait_for_testid_text


@pytest.fixture
def verified_teacher(unique_email, tracked_teachers):
    import db

    teacher = db.create_teacher(
        "E2E",
        "Login",
        unique_email,
        "Passw0rd!",
        "Test School",
        "",
        "United States",
        "Ohio",
        "Columbus",
    )
    db.mark_teacher_verified(teacher["teacher_id"])
    tracked_teachers.append(teacher["teacher_id"])
    return teacher


def test_valid_login_lands_on_app(page, base_url, verified_teacher, case):
    case.set(input={"email": verified_teacher["email"], "password": "Passw0rd!"})
    page.goto(f"{base_url}/login")
    page.get_by_test_id("tab-signin").click()
    page.get_by_test_id("auth-email").fill(verified_teacher["email"])
    page.get_by_test_id("auth-password").fill("Passw0rd!")
    page.get_by_test_id("auth-submit").click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
    case.expect(
        "/login" in page.url,
        False,
        input={"url": page.url},
        label="post-login URL should leave /login",
    )


def test_wrong_password_shows_error(page, base_url, verified_teacher, case):
    case.set(
        input={"email": verified_teacher["email"], "password": "WrongPass1!"},
        expected="non-empty auth error; stay on /login",
    )
    page.goto(f"{base_url}/login")
    page.get_by_test_id("tab-signin").click()
    page.get_by_test_id("auth-email").fill(verified_teacher["email"])
    page.get_by_test_id("auth-password").fill("WrongPass1!")
    page.get_by_test_id("auth-submit").click()
    text = wait_for_testid_text(page, "auth-error")
    case.expect(bool(text), True, input={"error": text, "url": page.url}, label="error message nonempty")
    case.expect("/login" in page.url, True, input={"url": page.url}, label="still on login")


def test_empty_login_fields_blocked_by_browser(page, base_url, case):
    """Email/password inputs are HTML-required; empty submit stays on login."""
    case.set(input={"email": "", "password": ""}, expected="HTML validity false; stay on /login")
    page.goto(f"{base_url}/login")
    page.get_by_test_id("tab-signin").click()
    page.get_by_test_id("auth-submit").click()
    case.expect("/login" in page.url, True, input={"url": page.url}, label="stay on login")
    case.expect(
        page.get_by_test_id("auth-error").inner_text().strip(),
        "",
        label="no JS error (browser blocks first)",
    )
    invalid = page.evaluate(
        """() => {
          const email = document.getElementById('email');
          const password = document.getElementById('password');
          return !email.checkValidity() || !password.checkValidity();
        }"""
    )
    case.expect(invalid, True, label="HTML5 required invalid")


def test_empty_password_only_blocked(page, base_url, verified_teacher, case):
    case.set(input={"email": verified_teacher["email"], "password": ""})
    page.goto(f"{base_url}/login")
    page.get_by_test_id("tab-signin").click()
    page.get_by_test_id("auth-email").fill(verified_teacher["email"])
    page.get_by_test_id("auth-submit").click()
    case.expect("/login" in page.url, True, input={"url": page.url}, label="stay on login")
    invalid = page.evaluate(
        "() => !document.getElementById('password').checkValidity()"
    )
    case.expect(invalid, True, label="password required invalid")


def test_logout_and_protected_redirect(page, base_url, verified_teacher, case):
    case.set(input={"email": verified_teacher["email"]})
    page.goto(f"{base_url}/login")
    page.get_by_test_id("tab-signin").click()
    page.get_by_test_id("auth-email").fill(verified_teacher["email"])
    page.get_by_test_id("auth-password").fill("Passw0rd!")
    page.get_by_test_id("auth-submit").click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=15000)

    page.goto(f"{base_url}/logout")
    page.wait_for_url("**/login**")
    case.expect_contains(page.url, "/login", label="after logout")

    page.goto(f"{base_url}/worksheet")
    page.wait_for_url("**/login**")
    case.expect_contains(page.url, "/login", label="worksheet requires auth")


def test_forgot_password_empty_email_shows_error(page, base_url, case):
    case.set(input={"forgot_email": ""}, expected="Enter your email")
    page.goto(f"{base_url}/login")
    page.get_by_test_id("tab-signin").click()
    page.get_by_test_id("forgot-open").click()
    page.get_by_test_id("forgot-form").wait_for(state="visible")
    page.get_by_test_id("forgot-email").fill("")
    page.get_by_test_id("forgot-submit").click()
    error = page.get_by_test_id("forgot-error")
    error.wait_for(state="visible")
    case.expect_contains(error.inner_text(), "Enter your email", label="forgot empty email")


def test_forgot_password_resets_via_mailpit(page, base_url, verified_teacher, require_mailpit, case):
    case.set(input={"email": verified_teacher["email"]})
    page.goto(f"{base_url}/login")
    page.get_by_test_id("tab-signin").click()
    page.get_by_test_id("forgot-open").click()
    page.get_by_test_id("forgot-email").fill(verified_teacher["email"])
    page.get_by_test_id("forgot-submit").click()
    page.get_by_test_id("reset-sent").wait_for(state="visible", timeout=15000)
    case.note("reset-sent screen visible")

    message = mailpit.wait_for_email(
        verified_teacher["email"], subject_contains="Reset"
    )
    link = mailpit.extract_link(message, "/reset")
    case.step("mailpit reset link", input={"link": link})
    page.goto(link)
    page.get_by_test_id("reset-form").wait_for(state="visible", timeout=15000)

    page.get_by_test_id("reset-password").fill("weak")
    page.get_by_test_id("reset-confirm").fill("weak")
    page.get_by_test_id("reset-submit").click()
    case.expect_contains(
        page.get_by_test_id("reset-error").inner_text().lower(),
        "letter",
        input={"password": "weak"},
        label="weak password rejected",
    )

    page.get_by_test_id("reset-password").fill("NewPass1!")
    page.get_by_test_id("reset-confirm").fill("Mismatch1!")
    page.get_by_test_id("reset-submit").click()
    case.expect_contains(
        page.get_by_test_id("reset-error").inner_text().lower(),
        "match",
        input={"password": "NewPass1!", "confirm": "Mismatch1!"},
        label="mismatch rejected",
    )

    page.get_by_test_id("reset-password").fill("NewPass1!")
    page.get_by_test_id("reset-confirm").fill("NewPass1!")
    page.get_by_test_id("reset-submit").click()
    case.expect_contains(
        wait_for_testid_text(page, "auth-error").lower(),
        "updated",
        label="password updated banner",
    )

    page.get_by_test_id("auth-email").fill(verified_teacher["email"])
    page.get_by_test_id("auth-password").fill("NewPass1!")
    page.get_by_test_id("auth-submit").click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
    case.expect(
        "/login" in page.url,
        False,
        input={"email": verified_teacher["email"], "password": "NewPass1!", "url": page.url},
        label="sign in with new password",
    )
