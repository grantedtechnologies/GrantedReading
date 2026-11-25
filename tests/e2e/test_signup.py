"""Signup, validation, email verification (Mailpit), and Stripe webhook simulation."""

from __future__ import annotations

import re

import pytest

from tests.e2e.helpers import db as e2e_db
from tests.e2e.helpers import mailpit, stripe_helpers


def _open_signup(page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    page.get_by_test_id("tab-signup").click()


def _fill_valid_signup(page, email: str, password: str = "Passw0rd!") -> None:
    page.get_by_test_id("signup-first-name").fill("E2E")
    page.get_by_test_id("signup-last-name").fill("Signup")
    page.get_by_test_id("signup-school").fill("E2E Elementary")
    page.get_by_test_id("signup-state").select_option(label="Ohio")
    page.get_by_test_id("signup-city").select_option(index=1)
    page.get_by_test_id("auth-email").fill(email)
    page.get_by_test_id("auth-password").fill(password)
    page.get_by_test_id("signup-confirm-password").fill(password)
    page.get_by_test_id("plan-standard").click()


def test_signup_empty_names_shows_error(page, base_url, unique_email, case):
    case.set(input={"first": "", "last": "", "email": unique_email}, expected="first and last name")
    _open_signup(page, base_url)
    page.get_by_test_id("signup-school").fill("E2E Elementary")
    page.get_by_test_id("signup-state").select_option(label="Ohio")
    page.get_by_test_id("signup-city").select_option(index=1)
    page.get_by_test_id("auth-email").fill(unique_email)
    page.get_by_test_id("auth-password").fill("Passw0rd!")
    page.get_by_test_id("signup-confirm-password").fill("Passw0rd!")
    page.get_by_test_id("auth-submit").click()
    error = page.get_by_test_id("auth-error")
    error.wait_for(state="visible")
    case.expect_contains(error.inner_text().lower(), "first and last name", label="empty names")
    case.expect(e2e_db.teacher_by_email(unique_email), None, label="no teacher created")


def test_signup_empty_school_shows_error(page, base_url, unique_email, case):
    case.set(input={"school": "", "email": unique_email}, expected="school")
    _open_signup(page, base_url)
    page.get_by_test_id("signup-first-name").fill("E2E")
    page.get_by_test_id("signup-last-name").fill("Signup")
    page.get_by_test_id("signup-state").select_option(label="Ohio")
    page.get_by_test_id("signup-city").select_option(index=1)
    page.get_by_test_id("auth-email").fill(unique_email)
    page.get_by_test_id("auth-password").fill("Passw0rd!")
    page.get_by_test_id("signup-confirm-password").fill("Passw0rd!")
    page.get_by_test_id("auth-submit").click()
    error = page.get_by_test_id("auth-error")
    error.wait_for(state="visible")
    case.expect_contains(error.inner_text().lower(), "school", label="empty school")
    case.expect(e2e_db.teacher_by_email(unique_email), None, label="no teacher created")


def test_signup_missing_state_city_shows_error(page, base_url, unique_email, case):
    case.set(input={"state": "", "city": "", "email": unique_email}, expected="state and city")
    _open_signup(page, base_url)
    page.get_by_test_id("signup-first-name").fill("E2E")
    page.get_by_test_id("signup-last-name").fill("Signup")
    page.get_by_test_id("signup-school").fill("E2E Elementary")
    page.get_by_test_id("auth-email").fill(unique_email)
    page.get_by_test_id("auth-password").fill("Passw0rd!")
    page.get_by_test_id("signup-confirm-password").fill("Passw0rd!")
    page.get_by_test_id("auth-submit").click()
    error = page.get_by_test_id("auth-error")
    error.wait_for(state="visible")
    text = error.inner_text().lower()
    case.expect_contains(text, "state", label="mentions state")
    case.expect_contains(text, "city", label="mentions city")
    case.expect(e2e_db.teacher_by_email(unique_email), None, label="no teacher created")


def test_signup_weak_password_shows_error(page, base_url, unique_email, case):
    case.set(input={"password": "password"}, expected="letter/number/symbol rule")
    _open_signup(page, base_url)
    _fill_valid_signup(page, unique_email, password="password")
    page.get_by_test_id("auth-submit").click()
    error = page.get_by_test_id("auth-error")
    error.wait_for(state="visible")
    text = error.inner_text().lower()
    case.set(actual=text)
    ok = "letter" in text or "symbol" in text or "number" in text
    case.expect(ok, True, input={"error": text}, label="weak password message")
    case.expect(e2e_db.teacher_by_email(unique_email), None, label="no teacher created")


def test_signup_password_mismatch_shows_error(page, base_url, unique_email, case):
    case.set(
        input={"password": "Passw0rd!", "confirm": "OtherPass1!"},
        expected="match",
    )
    _open_signup(page, base_url)
    page.get_by_test_id("signup-first-name").fill("E2E")
    page.get_by_test_id("signup-last-name").fill("Signup")
    page.get_by_test_id("signup-school").fill("E2E Elementary")
    page.get_by_test_id("signup-state").select_option(label="Ohio")
    page.get_by_test_id("signup-city").select_option(index=1)
    page.get_by_test_id("auth-email").fill(unique_email)
    page.get_by_test_id("auth-password").fill("Passw0rd!")
    page.get_by_test_id("signup-confirm-password").fill("OtherPass1!")
    page.get_by_test_id("auth-submit").click()
    error = page.get_by_test_id("auth-error")
    error.wait_for(state="visible")
    case.expect_contains(error.inner_text().lower(), "match", label="password mismatch")
    case.expect(e2e_db.teacher_by_email(unique_email), None, label="no teacher created")


def test_signup_pilot_without_code_shows_error(page, base_url, unique_email, case):
    case.set(input={"plan": "pilot", "pilot_code": ""}, expected="pilot code")
    _open_signup(page, base_url)
    _fill_valid_signup(page, unique_email)
    page.get_by_test_id("plan-pilot").click()
    page.get_by_test_id("auth-submit").click()
    error = page.get_by_test_id("auth-error")
    error.wait_for(state="visible")
    case.expect_contains(error.inner_text().lower(), "pilot code", label="pilot without code")
    case.expect(e2e_db.teacher_by_email(unique_email), None, label="no teacher created")


def test_signup_verifies_via_mailpit(
    page, base_url, unique_email, tracked_teachers, require_mailpit, case
):
    case.set(input={"email": unique_email, "plan": "standard"})
    _open_signup(page, base_url)
    _fill_valid_signup(page, unique_email)
    page.get_by_test_id("auth-submit").click()
    page.get_by_test_id("check-email").wait_for(state="visible", timeout=20000)

    teacher = e2e_db.teacher_by_email(unique_email)
    case.expect(teacher is not None, True, label="teacher row created")
    tracked_teachers.append(teacher["teacher_id"])
    case.expect(bool(teacher.get("verified")), False, label="unverified before link")

    message = mailpit.wait_for_email(unique_email, subject_contains="Confirm")
    link = mailpit.extract_link(message, "/verify")
    case.step("mailpit verify link", input={"link": link})
    page.goto(link)
    page.wait_for_url(lambda url: "/verify" not in url and "/login" not in url, timeout=20000)

    refreshed = e2e_db.teacher_by_email(unique_email)
    case.expect(bool(refreshed and refreshed.get("verified")), True, label="verified after link")


def test_pro_signup_redirects_to_stripe_and_webhook_marks_pro(
    page, base_url, unique_email, tracked_teachers, require_stripe, case
):
    case.set(input={"email": unique_email, "plan": "pro"})
    _open_signup(page, base_url)
    _fill_valid_signup(page, unique_email)
    page.get_by_test_id("plan-pro").click()

    with page.expect_response(re.compile(r".*/signup$")) as resp_info:
        page.get_by_test_id("auth-submit").click()
    data = resp_info.value.json()
    checkout = data.get("checkout_url") or ""
    case.expect(bool(checkout), True, input={"signup_json": data}, label="checkout_url present")
    case.expect(
        ("stripe.com" in checkout) or ("checkout.stripe.com" in checkout),
        True,
        input={"checkout_url": checkout},
        label="Stripe checkout URL",
    )

    teacher = e2e_db.teacher_by_email(unique_email)
    case.expect(teacher is not None, True, label="teacher row created")
    tracked_teachers.append(teacher["teacher_id"])

    event = stripe_helpers.subscription_created_event(teacher_id=teacher["teacher_id"])
    response = stripe_helpers.post_signed_event(base_url, event)
    case.expect(
        response.status_code,
        200,
        input={"body": response.text[:300]},
        label="signed webhook accepted",
    )

    import billing

    case.expect(billing.is_paid_pro(teacher["teacher_id"]), True, label="is_paid_pro")


@pytest.mark.stripe_cli
def test_stripe_cli_placeholder():
    pytest.skip("Run with `stripe listen --forward-to <base>/webhook` and remove this skip.")
