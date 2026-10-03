import base64
import io
import json
import logging
import os
import re
import time

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError
import requests
import stripe

import ai
import billing
import blobStorage
import cache
import db
import file
from reading_level import ReadingLevelError

load_dotenv(override=True)
ai.require_foundry_config()

CONFIRM_SALT = "email-confirm"
CONFIRM_MAX_AGE = 24 * 60 * 60
EMAIL_SEND_FAILED = (
    "We could not send the confirmation email. Your account is not verified yet, "
    "so you can try again."
)


def _env_text(name, default=""):
    return (os.getenv(name) or default).strip().strip("'\"")


MAILTRAP_SEND_URL = "https://send.api.mailtrap.io/api/send"
MAIL_SEND_TIMEOUT = 15


def _mail_sender():
    """From address Mailtrap will accept: a verified sending domain.

    A bare MAIL_DEFAULT_SENDER email becomes (display name, email).
    'Name <email>' is split the same way.
    """
    raw = (app.config.get("MAIL_DEFAULT_SENDER") or "").strip()
    match = re.fullmatch(r'"?([^"<]+?)"?\s*<([^>]+)>', raw)
    if match:
        return (match.group(1).strip(), match.group(2).strip())
    if raw:
        return ("Granted Reading", raw)
    return ("Granted Reading", "noreply@grantedreading.test")


def _send_mailtrap(subject, text, to_email):
    """Send via Mailtrap's HTTPS API. Raises on network or API failure."""
    token = app.config.get("MAIL_PASSWORD") or ""
    if not token:
        raise RuntimeError("MAIL_PASSWORD is not set.")
    name, email = _mail_sender()
    response = requests.post(
        MAILTRAP_SEND_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "from": {"email": email, "name": name},
            "to": [{"email": to_email}],
            "subject": subject,
            "text": text,
            "category": "email_verification",
        },
        timeout=MAIL_SEND_TIMEOUT,
    )
    response.raise_for_status()
    return response


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "granted-dev-secret")
app.config["MAIL_PASSWORD"] = _env_text("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = _env_text(
    "MAIL_DEFAULT_SENDER",
    "Granted Reading <noreply@grantedreading.test>",
)


def generate_confirmation_token(email):
    serializer = URLSafeTimedSerializer(app.secret_key)
    return serializer.dumps(email, salt=CONFIRM_SALT)


def confirm_token(token, max_age=CONFIRM_MAX_AGE):
    serializer = URLSafeTimedSerializer(app.secret_key)
    try:
        return serializer.loads(token, salt=CONFIRM_SALT, max_age=max_age)
    except Exception:
        return None

OUTPUT_DIR = "worksheets"

def save_worksheet_pdf(pdf_bytes, title):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_title = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_") or "worksheet"
    filename = f"{safe_title}_{int(time.time())}.pdf"
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "wb") as f:
        f.write(pdf_bytes)
    return path


# A second click that arrives while the first save is still uploading waits
# this long for the first one to finish before giving up.
SAVE_WAIT_SECONDS = 20
FEEDBACK_MAX_CHARS = 5000
RATING_MIN, RATING_MAX = 1, 5
GENERATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def _json_error(exc, *, resource="foundry", status=500, log="Request failed"):
    app.logger.exception(log)
    return jsonify({"error": ai.user_error_message(exc, resource)}), status


def _phonics_value(raw):
    """The letter pattern actually used, or None when none was given."""
    pattern = ai.parse_focus_phonics(raw)
    return pattern[:255] or None


def _level_number(value):
    raw = "" if value is None else str(value).strip().upper()
    if raw in ("K", "KINDERGARTEN"):
        return 0
    try:
        return int(float(raw))
    except ValueError:
        return None


def _measured_level(estimated_grade):
    return max(0, int(round(float(estimated_grade))))


def _optional_student_id(teacher_id, raw):
    if raw in (None, ""):
        return None
    try:
        student_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("That student is not on your account.") from exc
    if not db.student_belongs_to_teacher(teacher_id, student_id):
        raise ValueError("That student is not on your account.")
    return student_id


def _optional_vocab_list_id(teacher_id, raw):
    if raw in (None, ""):
        return None
    try:
        vocab_list_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("That vocab is not on your account.") from exc
    if not db.vocab_list_belongs_to_teacher(teacher_id, vocab_list_id):
        raise ValueError("That vocab is not on your account.")
    return vocab_list_id


def _worksheet_vocab(sheet):
    name = (sheet.get("vocab_list_name") or "").strip()
    words = (sheet.get("vocab_words") or "").strip()
    typed = (sheet.get("focus_vocabulary") or "").strip()
    if typed in ("null", "None"):
        typed = ""
    if name and words:
        return f"{name} · {words}"
    if words:
        return words
    if name:
        return name
    return typed


def _pdf_filename(title):
    safe = re.sub(r"[^\w\s-]", "", title or "").strip().replace(" ", "_")
    return f"{safe or 'worksheet'}.pdf"


def _pdf_response(pdf_bytes, title, as_attachment):
    response = send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=as_attachment,
        download_name=_pdf_filename(title),
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _wait_for_saved_sheet(teacher_id, generation_id):
    deadline = time.monotonic() + SAVE_WAIT_SECONDS
    while time.monotonic() < deadline:
        sheet = db.find_worksheet_by_generation(teacher_id, generation_id)
        if sheet is not None:
            return sheet
        time.sleep(0.5)
    return None


def _save_generation(teacher_id, generation_id):
    """Return (sheet, pdf_bytes), creating the row and blob only once.

    Raises LookupError when the generation is unknown, expired, or belongs
    to another teacher.
    """
    pending = cache.load_pending_worksheet(generation_id)
    if pending is not None and pending["teacher_id"] != teacher_id:
        pending = None

    existing = db.find_worksheet_by_generation(teacher_id, generation_id)
    if existing is not None:
        if pending is not None:
            return existing, pending["pdf_bytes"]
        return existing, blobStorage.download_pdf(existing["bloburl"])
    if pending is None:
        raise LookupError(generation_id)

    pdf_bytes = pending["pdf_bytes"]
    if not cache.acquire_save_lock(generation_id):
        sheet = _wait_for_saved_sheet(teacher_id, generation_id)
        if sheet is None:
            raise RuntimeError("Another save for this worksheet did not finish.")
        return sheet, pdf_bytes

    uploaded = []

    def store_pdf(sheet_id):
        key = blobStorage.worksheet_key(teacher_id, sheet_id)
        blobStorage.upload_pdf(key, pdf_bytes)
        uploaded.append(key)
        return key

    try:
        existing = db.find_worksheet_by_generation(teacher_id, generation_id)
        if existing is not None:
            return existing, pdf_bytes
        try:
            sheet = db.insert_worksheet(
                teacher_id, generation_id, pending["record"], store_pdf
            )
        except IntegrityError:
            sheet = db.find_worksheet_by_generation(teacher_id, generation_id)
            if sheet is None:
                raise
        return sheet, pdf_bytes
    except Exception:
        for key in uploaded:
            try:
                blobStorage.delete_pdf(key)
            except Exception:
                app.logger.exception("Could not remove orphaned blob %s", key)
        raise
    finally:
        cache.release_save_lock(generation_id)


def _dok_label(sheet):
    mix = sheet.get("dokMix") or {}
    mix_parts = [
        f"DOK {level}"
        for level in (1, 2, 3, 4)
        if mix.get(str(level), 0)
    ]
    if sheet["original"]:
        if mix_parts:
            count = ""
            if isinstance(mix.get("count"), int):
                count = f" · {mix['count']} questions"
            return "Custom: " + ", ".join(mix_parts) + count
        return f"DOK {sheet['targetDOKLevel']}" if sheet["targetDOKLevel"] else "—"
    parts = [
        f"DOK {level}: {mix.get(str(level), 0)}"
        for level in (1, 2, 3, 4)
        if mix.get(str(level), 0)
    ]
    return ", ".join(parts) if parts else "No questions detected"


def _level_label(level):
    if level is None:
        return "—"
    return "K" if int(level) == 0 else str(level)


def _history_item(sheet):
    student = " ".join(
        part for part in (sheet.get("student_first_name"), sheet.get("student_last_name")) if part
    )
    return {
        "sheetid": sheet["sheetid"],
        "title": sheet["title"],
        "created_at": sheet["createdAt"].isoformat() + "Z",
        "expected_level": _level_label(sheet["expectedLevel"]),
        "true_level": _level_label(sheet["trueLevel"]),
        "sheettype": sheet["sheettype"],
        "flow": "Generated" if sheet["original"] else "Leveled from upload",
        "dok": _dok_label(sheet),
        "phonics": sheet.get("phonics") or "",
        "informational": sheet["informational"],
        "interest": sheet["interest"] or "",
        "student": student or "General / no student assigned",
        "student_id": sheet.get("studentid") or "",
        "class_id": sheet.get("class_id") or "",
        "class_name": sheet.get("class_name") or "",
        "vocab": _worksheet_vocab(sheet),
        "feedback": sheet["feedback"] or "",
        "rating": sheet["rating"],
    }


def _teacher_session(teacher):
    session["teacher_id"] = teacher["teacher_id"]
    session["teacher_name"] = f"{teacher['first_name']} {teacher['last_name']}"
    # Pro is the latest subscription row, not a column on Teachers.
    session["membership"] = "pro" if billing.is_teacher_pro(teacher["teacher_id"]) else "standard"


def _verified_teacher():
    teacher_id = session.get("teacher_id")
    if not teacher_id:
        return None
    teacher = db.find_teacher_by_id(teacher_id)
    if teacher is None or not teacher.get("verified"):
        session.clear()
        return None
    _teacher_session(teacher)
    return teacher


def _require_teacher_id():
    teacher = _verified_teacher()
    if teacher is None:
        return None, (jsonify({
            "error": "Confirm your email before using your account.",
        }), 403)
    return teacher["teacher_id"], None


ACCOUNT_ENDPOINTS = {
    "worksheet",
    "account",
    "api_account",
    "api_update_account",
    "api_students",
    "api_create_student",
    "api_update_student",
    "api_delete_student",
    "api_class_students",
    "class_roster",
    "student_profile",
    "new_student",
    "api_create_class",
    "api_delete_class",
    "setup",
    "api_create_vocab_list",
    "api_add_vocab_words",
    "api_update_vocab_list",
    "api_delete_vocab_list",
    "generate",
    "download_worksheet",
    "view_worksheet",
    "worksheet_history",
    "api_worksheet_feedback",
}


# Demo deployment: keep the Reading Level Corrector code, but do not serve it.
DEMO_HIDDEN_ENDPOINTS = {
    "reading_level_corrector",
    "lexile_corrector_redirect",
    "correct_reading_level",
    "segment_worksheet",
    "modulate_worksheet",
    "generate_leveled_passage",
}


@app.before_request
def _enforce_verified_account():
    if request.endpoint in (None, "static"):
        return None
    if request.endpoint in DEMO_HIDDEN_ENDPOINTS:
        abort(404)
    teacher = _verified_teacher() if session.get("teacher_id") else None
    if request.endpoint not in ACCOUNT_ENDPOINTS:
        return None
    if teacher is None:
        if request.path.startswith("/api/") or request.endpoint in (
            "generate",
            "download_worksheet",
        ):
            return jsonify({
                "error": "Confirm your email before using your account.",
            }), 403
        return redirect("/login")


def _password_problem(password, confirm):
    if password != confirm:
        return "Those passwords do not match."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password) or not re.search(r"[^A-Za-z0-9]", password):
        return "Password needs a letter, a number, and a symbol."
    return None


def _parse_grade(value, label):
    raw = "" if value is None else str(value).strip()
    if raw.upper() in ("K", "KINDERGARTEN"):
        raw = "0"
    if raw == "":
        raise ValueError(f"Choose a {label}.")
    try:
        grade = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Choose a {label}.") from exc
    if grade < 0 or grade > 12:
        raise ValueError(f"{label} must be kindergarten through 12.")
    return grade


def _parse_words(raw):
    if isinstance(raw, list):
        parts = raw
    else:
        parts = re.split(r"[\n,]", raw or "")
    words = []
    seen = set()
    for part in parts:
        word = " ".join(str(part).split())
        key = word.lower()
        if word and key not in seen:
            seen.add(key)
            words.append(word)
    return words


@app.route("/")
def index():
    if session.get("teacher_id"):
        return redirect("/worksheet")
    return render_template(
        "login.html",
        initial_mode="signup",
        billing_canceled=request.args.get("billing") == "canceled",
        resume_checkout=request.args.get("billing") == "canceled" and bool(session.get("pending_checkout_teacher_id")),
    )


@app.route("/worksheet")
def worksheet():
    return render_template("index.html", active_page="worksheet")


@app.route("/api/students")
def api_students():
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    return jsonify({"students": db.list_students_for_teacher(teacher_id)})


@app.route("/login")
def login_page():
    if session.get("teacher_id"):
        return redirect("/worksheet")
    return render_template("login.html", initial_mode="signin")


@app.route("/login", methods=["POST"])
def login():
    body = request.get_json() or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not email or not password:
        return jsonify({"error": "Enter an email and password."}), 400

    teacher = db.find_teacher_by_email(email)
    if teacher is None or teacher["password"] != password:
        return jsonify({"error": "That email or password is not right."}), 401

    if not teacher.get("verified"):
        sent = _issue_verification(teacher, request.host_url)
        if sent:
            return jsonify({
                "error": "Confirm your email before signing in. We sent a new link.",
                "needs_verification": True,
                "email_sent": True,
            }), 403
        return jsonify({
            "error": EMAIL_SEND_FAILED,
            "needs_verification": True,
            "email_sent": False,
        }), 503

    _teacher_session(teacher)
    return jsonify({"ok": True, "redirect": "/worksheet"})


@app.route("/signup", methods=["POST"])
def signup():
    body = request.get_json() or {}
    first_name = (body.get("first_name") or "").strip()
    last_name = (body.get("last_name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    confirm = body.get("password_confirm") or ""

    if not first_name or not last_name:
        return jsonify({"error": "Enter a first and last name."}), 400
    if not email or not password:
        return jsonify({"error": "Enter an email and password."}), 400
    problem = _password_problem(password, confirm)
    if problem:
        return jsonify({"error": problem}), 400
    plan = (body.get("membership") or "standard").strip().lower()
    if plan not in ("standard", "pro"):
        return jsonify({"error": "Choose Standard or Pro."}), 400

    existing = db.find_teacher_by_email(email)
    if existing and existing.get("verified"):
        return jsonify({"error": "That email is already in use. Sign in instead."}), 409
    if existing:
        try:
            teacher = db.refresh_unverified_teacher(
                existing["teacher_id"], first_name, last_name, password
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
    else:
        try:
            teacher = db.create_teacher(first_name, last_name, email, password)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409

    if plan != "pro":
        sent = _issue_verification(teacher, request.host_url)
        if not sent:
            return jsonify({
                "error": EMAIL_SEND_FAILED,
                "needs_verification": True,
                "email_sent": False,
            }), 503
        return jsonify({"ok": True, "needs_verification": True, "email_sent": True})

    if billing.is_teacher_pro(teacher["teacher_id"]):
        sent = _issue_verification(teacher, request.host_url)
        if not sent:
            return jsonify({
                "error": EMAIL_SEND_FAILED,
                "needs_verification": True,
                "email_sent": False,
            }), 503
        return jsonify({"ok": True, "needs_verification": True, "email_sent": True})

    # Pro confirms the email after payment, so the pages run signup, checkout,
    # then the check-your-email screen.
    session["pending_checkout_teacher_id"] = teacher["teacher_id"]
    base = request.host_url.rstrip("/")
    try:
        checkout_url = billing.create_checkout_session(
            teacher,
            request.host_url,
            success_url=f"{base}/signup/paid?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base}/?billing=canceled",
        )
    except billing.BillingNotConfigured:
        app.logger.exception("Billing is not configured")
        return jsonify({"error": "Billing is not set up yet."}), 503
    except stripe.StripeError:
        app.logger.exception("Stripe Checkout session failed for new teacher %s", teacher["teacher_id"])
        return jsonify({"error": "Could not reach Stripe. Try again."}), 502
    return jsonify({"ok": True, "checkout_url": checkout_url})


@app.route("/signup/checkout", methods=["POST"])
def signup_resume_checkout():
    teacher_id = session.get("pending_checkout_teacher_id")
    teacher = db.find_teacher_by_id(teacher_id) if teacher_id else None
    if teacher is None or teacher.get("verified"):
        return jsonify({"error": "Start signup again."}), 400
    base = request.host_url.rstrip("/")
    try:
        checkout_url = billing.create_checkout_session(
            teacher,
            request.host_url,
            success_url=f"{base}/signup/paid?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base}/?billing=canceled",
        )
    except billing.BillingNotConfigured:
        app.logger.exception("Billing is not configured")
        return jsonify({"error": "Billing is not set up yet."}), 503
    except stripe.StripeError:
        app.logger.exception("Stripe Checkout session failed for teacher %s", teacher_id)
        return jsonify({"error": "Could not reach Stripe. Try again."}), 502
    return jsonify({"checkout_url": checkout_url})


@app.route("/signup/paid")
def signup_paid():
    session_id = (request.args.get("session_id") or "").strip()
    teacher_id = None
    if session_id:
        try:
            teacher_id = billing.record_checkout(session_id)
        except billing.BillingNotConfigured:
            app.logger.exception("Billing is not configured")
            return render_template("login.html", initial_mode="signin", verify_error=True)
        except stripe.StripeError:
            app.logger.exception("Could not read Checkout session %s", session_id)
            return redirect("/?billing=canceled")
    if not teacher_id:
        return redirect("/?billing=canceled")
    try:
        teacher = db.find_teacher_by_id(int(teacher_id))
    except (TypeError, ValueError):
        teacher = None
    if teacher is None:
        return redirect("/")
    session.pop("pending_checkout_teacher_id", None)
    email_sent = True
    if not teacher.get("verified"):
        email_sent = _issue_verification(teacher, request.host_url)
    return render_template(
        "login.html",
        initial_mode="signin",
        show_check_email=True,
        pending_email=teacher["email"],
        email_send_failed=not email_sent,
    )


def _issue_verification(teacher, host_url):
    """Send the confirm-email link. Returns False when Mailtrap does not accept it."""
    token = generate_confirmation_token(teacher["email"])
    link = f"{host_url.rstrip('/')}/verify?token={token}"
    greeting = teacher.get("first_name") or "there"
    body = (
        f"Hi {greeting},\n\n"
        "Confirm your Granted Reading email by opening this link:\n\n"
        f"{link}\n\n"
        "This link expires in 24 hours.\n"
    )
    try:
        _send_mailtrap(
            "Confirm your Granted Reading account",
            body,
            teacher["email"],
        )
    except Exception:
        app.logger.exception("Could not send verification email to %s", teacher["email"])
        app.logger.info("Verification link for %s: %s", teacher["email"], link)
        return False
    app.logger.info("Verification link for %s: %s", teacher["email"], link)
    return True


@app.route("/verify")
def verify():
    token = (request.args.get("token") or "").strip()
    email = confirm_token(token) if token else None
    teacher = db.find_teacher_by_email(email) if email else None
    if teacher is None:
        return render_template("login.html", initial_mode="signin", verify_error=True)
    db.mark_teacher_verified(teacher["teacher_id"])
    teacher["verified"] = 1
    _teacher_session(teacher)
    if db.list_classes_for_teacher(teacher["teacher_id"]):
        return redirect("/worksheet")
    return redirect("/setup")


@app.route("/resend-verification", methods=["POST"])
def resend_verification():
    body = request.get_json() or {}
    email = (body.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "Enter the email you signed up with."}), 400
    teacher = db.find_teacher_by_email(email)
    if teacher is None or teacher.get("verified"):
        return jsonify({"ok": True, "email_sent": True})
    sent = _issue_verification(teacher, request.host_url)
    if not sent:
        return jsonify({"error": EMAIL_SEND_FAILED, "email_sent": False}), 503
    return jsonify({"ok": True, "needs_verification": True, "email_sent": True})


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/account")
def account():
    teacher_id = session.get("teacher_id")
    if not teacher_id:
        return redirect("/login")
    teacher = db.find_teacher_by_id(teacher_id)
    if teacher is None:
        session.clear()
        return redirect("/login")
    session_id = (request.args.get("session_id") or "").strip()
    if session_id or request.args.get("billing") == "success":
        try:
            if session_id:
                billing.record_checkout(session_id, teacher_id)
            elif not billing.is_teacher_pro(teacher_id):
                billing.sync_teacher_from_stripe(teacher)
        except billing.BillingNotConfigured:
            app.logger.exception("Billing is not configured")
        except stripe.StripeError:
            app.logger.exception("Could not record the subscription for teacher %s", teacher_id)
        _teacher_session(teacher)
    return render_template("account.html", active_page="account")


@app.route("/api/account")
def api_account():
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    teacher = db.find_teacher_by_id(teacher_id)
    if teacher is None:
        session.clear()
        return jsonify({"error": "Sign in to continue."}), 401
    try:
        subscription = db.latest_subscription(teacher_id)
        billing_state = {
            "is_pro": billing.is_teacher_pro(teacher_id),
            "can_manage": subscription is not None,
            "plan_name": billing.PLAN_NAME,
            "price_cents": billing.PLAN_PRICE_CENTS,
            "interval": billing.PLAN_INTERVAL,
        }
    except Exception:
        app.logger.exception("Could not read billing status for teacher %s", teacher_id)
        billing_state = None
    return jsonify({
        "teacher": teacher,
        "classes": db.list_classes_for_teacher(teacher_id),
        "students": db.list_students_for_teacher(teacher_id),
        "billing": billing_state,
    })


@app.route("/api/account", methods=["POST"])
def api_update_account():
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    body = request.get_json() or {}
    first_name = (body.get("first_name") or "").strip()
    last_name = (body.get("last_name") or "").strip()
    if not first_name or not last_name:
        return jsonify({"error": "Enter a first and last name."}), 400
    teacher = db.update_teacher(teacher_id, first_name, last_name)
    _teacher_session(teacher)
    return jsonify({"ok": True, "teacher": teacher})


@app.route("/billing/create-checkout-session", methods=["POST"])
def billing_create_checkout_session():
    teacher = _verified_teacher()
    if teacher is None:
        return jsonify({"error": "Confirm your email before using your account."}), 403
    try:
        if billing.is_teacher_pro(teacher["teacher_id"]):
            return jsonify({
                "error": f"You already have {billing.PLAN_NAME}. Use Manage subscription to change it.",
            }), 409
        url = billing.create_checkout_session(teacher, request.host_url)
    except billing.BillingNotConfigured:
        app.logger.exception("Billing is not configured")
        return jsonify({"error": "Billing is not set up yet."}), 503
    except stripe.StripeError:
        app.logger.exception("Stripe Checkout session failed for teacher %s", teacher["teacher_id"])
        return jsonify({"error": "Could not reach Stripe. Try again."}), 502
    return jsonify({"url": url})


@app.route("/billing/create-portal-session", methods=["POST"])
def billing_create_portal_session():
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    subscription = db.latest_subscription(teacher_id)
    if subscription is None:
        return jsonify({"error": "You do not have a subscription to manage yet."}), 404
    try:
        url = billing.create_portal_session(
            subscription["stripe_customer_id"],
            request.host_url.rstrip("/") + "/account",
        )
    except billing.BillingNotConfigured:
        app.logger.exception("Billing is not configured")
        return jsonify({"error": "Billing is not set up yet."}), 503
    except stripe.StripeError:
        app.logger.exception("Stripe portal session failed for teacher %s", teacher_id)
        return jsonify({"error": "Could not reach Stripe. Try again."}), 502
    return jsonify({"url": url})


@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    try:
        event = billing.parse_event(
            request.get_data(),
            request.headers.get("Stripe-Signature"),
        )
    except billing.BillingNotConfigured:
        app.logger.exception("Stripe webhook secret is missing")
        return jsonify({"error": "Webhook is not configured."}), 500
    except (ValueError, stripe.SignatureVerificationError):
        app.logger.warning("Rejected a Stripe webhook with a bad signature or payload")
        return jsonify({"error": "Invalid signature."}), 400
    try:
        outcome = billing.handle_event(event)
    except Exception:
        # A 500 makes Stripe retry. The event id is only recorded when its
        # change commits, so the retry is not skipped as a duplicate.
        app.logger.exception("Stripe webhook %s (%s) failed", event.get("id"), event.get("type"))
        return jsonify({"error": "Could not process event."}), 500
    app.logger.info("Stripe webhook %s (%s): %s", event["id"], event["type"], outcome)
    return jsonify({"received": True})


def _class_fields(body):
    name = (body.get("name") or "").strip()
    subject = (body.get("subject") or "").strip()
    school_year = (body.get("school_year") or "").strip()
    if not name or not subject:
        raise ValueError("Enter a class name and subject.")
    if len(name) > 255 or len(subject) > 255:
        raise ValueError("Keep the class name and subject under 255 characters.")
    if not re.fullmatch(r"\d{4}", school_year):
        raise ValueError("Enter the school year as four digits, like 2026.")
    grade_level = _parse_grade(body.get("grade_level"), "grade level")
    return name, subject, grade_level, school_year


def _split_student_name(raw):
    parts = (raw or "").split()
    if not parts:
        raise ValueError("Enter the student's name.")
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


def _student_fields(body):
    first_name = (body.get("first_name") or "").strip()
    last_name = (body.get("last_name") or "").strip()
    full_name = (body.get("name") or "").strip()
    if full_name and not first_name and not last_name:
        first_name, last_name = _split_student_name(full_name)
    notes = (body.get("notes") or "").strip()
    if not first_name or not last_name:
        raise ValueError("Enter the student's name.")
    classroom_grade = _parse_grade(body.get("classroom_grade"), "classroom grade")
    reading_level = _parse_grade(body.get("reading_level"), "reading level")
    return first_name, last_name, classroom_grade, reading_level, notes


def _student_display_name(student):
    first = (student.get("first_name") or "").strip()
    last = (student.get("last_name") or "").strip()
    if not last or last == first:
        return first
    return f"{first} {last}".strip()


def _class_page(teacher_id, class_id, student=None, is_new=False):
    classroom = db.find_class_for_teacher(teacher_id, class_id)
    if classroom is None:
        return None
    students = db.list_students_for_class(teacher_id, class_id)
    selected_name = _student_display_name(student) if student else ""
    return render_template(
        "class.html",
        active_page="classes",
        classroom=classroom,
        students=students,
        selected_student=student,
        selected_name=selected_name,
        is_new=is_new,
        classroom_id=classroom["class_id"],
    )


@app.route("/setup")
def setup():
    if not session.get("teacher_id"):
        return redirect("/login")
    from_account = (request.args.get("next") or "").strip() == "account"
    return render_template(
        "setup.html",
        active_page="account",
        after_setup="/account" if from_account else "/worksheet",
        after_label="Back to account" if from_account else "Continue to worksheets",
        from_account=from_account,
    )


@app.route("/api/classes", methods=["POST"])
def api_create_class():
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    try:
        name, subject, grade_level, school_year = _class_fields(request.get_json() or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    classroom = db.create_class(teacher_id, name, subject, grade_level, school_year)
    return jsonify({"ok": True, "class": classroom})


@app.route("/api/classes/<int:class_id>", methods=["DELETE"])
def api_delete_class(class_id):
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    try:
        db.delete_class(teacher_id, class_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.route("/classes/<int:class_id>")
def class_roster(class_id):
    teacher = _verified_teacher()
    if teacher is None:
        return redirect("/login")
    page = _class_page(teacher["teacher_id"], class_id)
    if page is None:
        return redirect("/account")
    return page


@app.route("/classes/<int:class_id>/students/new")
def new_student(class_id):
    teacher = _verified_teacher()
    if teacher is None:
        return redirect("/login")
    page = _class_page(teacher["teacher_id"], class_id, is_new=True)
    if page is None:
        return redirect("/account")
    return page


@app.route("/classes/<int:class_id>/students/<int:student_id>")
def student_profile(class_id, student_id):
    teacher = _verified_teacher()
    if teacher is None:
        return redirect("/login")
    student = db.find_student_for_teacher(teacher["teacher_id"], student_id)
    if student is None or int(student["class_id"]) != class_id:
        return redirect(f"/classes/{class_id}")
    page = _class_page(teacher["teacher_id"], class_id, student=student)
    if page is None:
        return redirect("/account")
    return page


@app.route("/api/classes/<int:class_id>/students")
def api_class_students(class_id):
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    classroom = db.find_class_for_teacher(teacher_id, class_id)
    if classroom is None:
        return jsonify({"error": "That class is not on your account."}), 404
    return jsonify({
        "class": classroom,
        "students": db.list_students_for_class(teacher_id, class_id),
    })


@app.route("/api/students", methods=["POST"])
def api_create_student():
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    body = request.get_json() or {}
    try:
        class_id = int(body.get("class_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Choose a class for this student."}), 400
    try:
        first_name, last_name, classroom_grade, reading_level, notes = _student_fields(body)
        student = db.create_student(
            teacher_id,
            class_id,
            first_name,
            last_name,
            classroom_grade,
            reading_level,
            notes=notes,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "student": student})


@app.route("/api/students/<int:student_id>", methods=["POST"])
def api_update_student(student_id):
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    body = request.get_json() or {}
    try:
        first_name, last_name, classroom_grade, reading_level, notes = _student_fields(body)
        student = db.update_student(
            teacher_id,
            student_id,
            first_name,
            last_name,
            classroom_grade,
            reading_level,
            notes=notes,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "student": student})


@app.route("/api/students/<int:student_id>", methods=["DELETE"])
def api_delete_student(student_id):
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    try:
        db.delete_student(teacher_id, student_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.route("/api/vocab-lists", methods=["POST"])
def api_create_vocab_list():
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    body = request.get_json() or {}
    list_name = (body.get("list_name") or "").strip()
    description = (body.get("description") or "").strip()
    words = _parse_words(body.get("words"))
    try:
        student_id = int(body.get("student_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Choose a student for this vocab."}), 400
    if not list_name:
        return jsonify({"error": "Enter a name."}), 400
    if not words:
        return jsonify({"error": "Add at least one word."}), 400
    try:
        vocab_list = db.create_vocab_list(
            teacher_id, student_id, list_name, description, words
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "vocab_list": vocab_list})


@app.route("/api/vocab-lists/<int:vocab_list_id>/words", methods=["POST"])
def api_add_vocab_words(vocab_list_id):
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    body = request.get_json() or {}
    words = _parse_words(body.get("words"))
    if not words:
        return jsonify({"error": "Add at least one word."}), 400
    try:
        added = db.add_words_to_list(teacher_id, vocab_list_id, words)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "words": added})


@app.route("/api/vocab-lists/<int:vocab_list_id>", methods=["PUT"])
def api_update_vocab_list(vocab_list_id):
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    body = request.get_json() or {}
    list_name = (body.get("list_name") or "").strip()
    description = (body.get("description") or "").strip()
    words = _parse_words(body.get("words"))
    if not list_name:
        return jsonify({"error": "Enter a name."}), 400
    if not words:
        return jsonify({"error": "Add at least one word."}), 400
    try:
        vocab_list = db.update_vocab_list(
            teacher_id, vocab_list_id, list_name, description, words
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "vocab_list": vocab_list})


@app.route("/api/vocab-lists/<int:vocab_list_id>", methods=["DELETE"])
def api_delete_vocab_list(vocab_list_id):
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    try:
        db.delete_vocab_list(teacher_id, vocab_list_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.route("/reading-level-corrector")
def reading_level_corrector():
    return render_template(
        "reading_level_corrector.html", active_page="reading_level"
    )


@app.route("/how-reading-level-works")
def how_reading_level_works():
    return render_template(
        "how_reading_level_works.html", active_page="how_reading_level"
    )


@app.route("/lexile-corrector")
def lexile_corrector_redirect():
    return redirect("/reading-level-corrector", code=301)


@app.route("/correct-reading-level", methods=["POST"])
def correct_reading_level():
    """Measure and repair pasted text.

    Deterministic by default: no model call unless `allow_rewrite` is set, in
    which case a failing passage escalates to the LLM rewrite pass. Unlike the
    generation route this never returns an error for a passage it could not fix
    -- a teacher is reading the output here, and a partial improvement plus an
    honest "still above band" flag is more useful than a refusal.
    """
    body = request.get_json() or {}

    text = (body.get("text") or "").strip()
    target_grade = body.get("target_grade")
    allow_rewrite = bool(body.get("allow_rewrite"))
    # Left as None when the form does not say. correct_with_rewrite treats that
    # as "assume the strict tier and warn" rather than picking the permissive
    # one, since nothing about pasted text reveals whether it is a story.
    passage_type = body.get("passage_type") or None
    protected_terms = [
        term.strip()
        for term in (body.get("protected_terms") or "").split(",")
        if term.strip()
    ]

    if not text:
        return jsonify({"error": "Enter some text to correct."}), 400
    if target_grade in (None, ""):
        return jsonify({"error": "Choose a target grade level."}), 400

    try:
        outcome = ai.correct_with_rewrite(
            text,
            target_grade,
            protected_terms=protected_terms,
            allow_rewrite=allow_rewrite,
            passage_type=passage_type,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except ReadingLevelError as exc:
        return _json_error(exc, log="Reading level analysis is unavailable")
    except Exception as exc:
        return _json_error(exc, log="Reading level correction failed")

    result = outcome.correction
    payload = {
        "corrected_text": outcome.text,
        "reading_level": {
            "estimated_grade": round(outcome.final_score.estimated_grade, 2),
            "target_band": result.target_band.display,
            "in_band": outcome.gate_passed,
            "confidence": outcome.final_score.confidence,
        },
        "initial_reading_level": {
            "estimated_grade": round(result.initial_score.estimated_grade, 2),
            "confidence": result.initial_score.confidence,
        },
        "gate_passed": outcome.gate_passed,
        "failure_reason": outcome.failure_reason,
        "llm_rewrite_applied": outcome.llm_rewrite_applied,
    }
    if outcome.failure_reason == "rewrite_call_failed":
        payload["error"] = ai.FOUNDRY_DOWN
    return jsonify(payload)


@app.route("/segment-worksheet", methods=["POST"])
def segment_worksheet():
    """Split an uploaded worksheet into typed blocks for teacher review."""
    upload = request.files.get("worksheet")
    if upload is None or not upload.filename:
        return jsonify({"error": "Upload a .docx or .pdf worksheet."}), 400
    data = upload.read()
    if not data:
        return jsonify({"error": "That file was empty."}), 400
    use_llm = (request.form.get("use_llm") or "false").lower() == "true"
    try:
        blocks = ai.segment_worksheet(data, upload.filename, use_llm=use_llm)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        if use_llm:
            return _json_error(exc, log="Worksheet segmentation failed")
        app.logger.exception("Worksheet segmentation failed")
        return jsonify({"error": "Could not read that worksheet."}), 500
    return jsonify({
        "blocks": [block.to_dict() for block in blocks],
        "filename": upload.filename,
    })


@app.route("/modulate-worksheet", methods=["POST"])
def modulate_worksheet():
    """Level a confirmed worksheet (passages, questions, answer choices)."""
    body = request.get_json() or {}
    blocks = body.get("blocks") or []
    target_grade = body.get("target_grade")
    worksheet_kind = (body.get("worksheet_kind") or "practice").strip().lower()
    source_material = body.get("source_material") or ""
    protected_terms = [
        term.strip()
        for term in (body.get("protected_terms") or "").split(",")
        if term.strip()
    ]
    dok_mix = body.get("dok_mix")
    if dok_mix is not None:
        try:
            dok_mix = {int(level): int(share) for level, share in dok_mix.items()}
        except (TypeError, ValueError):
            return jsonify({"error": "Custom DOK mix must be four whole percents."}), 400
        if sum(dok_mix.get(level, 0) for level in (1, 2, 3, 4)) != 100:
            return jsonify({"error": "Custom DOK mix must sum to 100."}), 400

    if not blocks:
        return jsonify({"error": "Confirm the detected blocks before continuing."}), 400
    if target_grade in (None, ""):
        return jsonify({"error": "Choose a target grade level."}), 400

    teacher = _verified_teacher()
    student_id = None
    if teacher is not None:
        try:
            student_id = _optional_student_id(teacher["teacher_id"], body.get("student_id"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    try:
        result = ai.modulate_worksheet(
            blocks,
            target_grade,
            source_material=source_material,
            worksheet_kind=worksheet_kind,
            dok_mix=dok_mix,
            protected_terms=protected_terms,
            allow_rewrite=bool(body.get("allow_rewrite", True)),
            passage_type=body.get("passage_type") or None,
            convert_questions=bool(body.get("convert_questions")),
            title=body.get("title") or "Worksheet",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except ReadingLevelError as exc:
        return _json_error(exc, log="Worksheet modulation failed")
    except Exception as exc:
        return _json_error(exc, log="Worksheet modulation failed")

    if teacher is not None:
        try:
            result["generation_id"] = _stash_modulated_worksheet(
                teacher["teacher_id"], result, body, target_grade, student_id
            )
        except Exception:
            app.logger.exception("Could not hold the leveled worksheet for saving")
            result["generation_id"] = None
    return jsonify(result)


def _stash_modulated_worksheet(teacher_id, result, body, target_grade, student_id):
    expected_level = _level_number(target_grade)
    passage_grades = [row["final_grade"] for row in result.get("passages") or []]
    if passage_grades:
        true_level = _measured_level(sum(passage_grades) / len(passage_grades))
    else:
        true_level = expected_level
    achieved = result["dok"]["achieved_counts"]
    blocks = [
        {key: value for key, value in block.items() if key != "asset_base64"}
        for block in result.get("blocks") or []
    ]
    return cache.stash_pending_worksheet(
        teacher_id,
        base64.b64decode(result["pdf_base64"]),
        {
            "original": False,
            "sheettype": "worksheet",
            "title": (result.get("title") or "Worksheet")[:255],
            "expectedLevel": expected_level,
            "trueLevel": true_level,
            "targetDOKLevel": None,
            "dokMix": {str(level): int(achieved.get(level, 0)) for level in (1, 2, 3, 4)},
            "phonics": None,
            "informational": (body.get("passage_type") or "") == "informational",
            "interest": "",
            "studentid": student_id,
            "vocabListid": None,
            "sourceurl": None,
            "content": json.dumps({
                "title": result.get("title"),
                "worksheet_kind": body.get("worksheet_kind") or "practice",
                "blocks": blocks,
                "dok_after": result["dok"]["after"],
            }),
        },
    )


@app.route("/generate-leveled-passage", methods=["POST"])
def generate_leveled_passage():
    """Generate a passage and gate it on measured reading level."""
    body = request.get_json() or {}

    topic = (body.get("topic") or "").strip()
    target_grade = body.get("target_grade")
    student_interest = (body.get("student_interest") or "").strip() or None
    protected_terms = [
        term.strip()
        for term in (body.get("protected_terms") or "").split(",")
        if term.strip()
    ]

    if not topic:
        return jsonify({"error": "Enter a topic."}), 400
    if target_grade in (None, ""):
        return jsonify({"error": "Choose a target grade level."}), 400

    try:
        result = ai.generate_leveled_passage(
            topic,
            target_grade,
            protected_terms=protected_terms,
            student_interest=student_interest,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except ReadingLevelError as exc:
        # A content-quality failure, not a server fault.
        status = 422 if exc.reason else 500
        return jsonify({"error": str(exc), "reason": exc.reason}), status
    except Exception as exc:
        return _json_error(exc, log="Leveled passage generation failed")

    return jsonify({
        "title": result["title"],
        "passage": result["passage"],
        "questions": result["questions"],
        "question_levels": result["question_levels"],
        "reading_level": result["reading_level"],
        "corrections_applied": result["corrections_applied"],
        "protected_terms_preserved": result["protected_terms_preserved"],
        "llm_rewrite_applied": result.get("llm_rewrite_applied", False),
        "naturalness": result.get("naturalness"),
    })


def _stash_or_none(teacher_id, pdf_bytes, record):
    try:
        return cache.stash_pending_worksheet(teacher_id, pdf_bytes, record)
    except Exception:
        app.logger.exception("Could not hold the generated worksheet for saving")
        return None


@app.route("/generate", methods=["POST"])
def generate():
    teacher_id, error = _require_teacher_id()
    if error:
        return error

    is_pro = billing.is_teacher_pro(teacher_id)
    session["membership"] = "pro" if is_pro else "standard"
    if not is_pro:
        try:
            if cache.worksheet_quota_used(teacher_id):
                return jsonify({
                    "error": "Standard includes one worksheet a day. Upgrade to Pro for more.",
                }), 429
        except Exception:
            app.logger.exception("Redis quota check failed")
            return jsonify({"error": "Could not check your daily worksheet limit."}), 503

    body = request.get_json() or {}

    grade = body.get("grade", "")
    reading_level = body.get("reading_level", "")
    interests = body.get("interests", "")
    focus_vocabulary = body.get("focus_vocabulary", "")
    focus_phonics = body.get("focus_phonics", "")
    dok_raw = body.get("dok_level", 1)
    if str(dok_raw).strip().lower() == "custom":
        dok_levels = ai.parse_dok_levels(body.get("dok_levels") or [])
        if not body.get("dok_levels"):
            return jsonify({"error": "Pick at least one question type."}), 400
    else:
        dok_levels = [ai.parse_dok_level(dok_raw)]
    question_count = ai.parse_question_count(body.get("question_count", 5))
    if body.get("question_count") not in (None, ""):
        try:
            requested = int(body.get("question_count"))
        except (TypeError, ValueError):
            return jsonify({"error": "Choose how many questions you want."}), 400
        if requested < 1 or requested > ai.MAX_WORKSHEET_QUESTIONS:
            return jsonify({"error": "Choose 1 to 8 questions."}), 400
        question_count = requested

    expected_level = _level_number(reading_level)
    if expected_level is None:
        return jsonify({"error": "Please select a reading level."}), 400
    try:
        student_id = _optional_student_id(teacher_id, body.get("student_id"))
        vocab_list_id = _optional_vocab_list_id(teacher_id, body.get("vocab_list_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        model_data, pdf_bytes, image_base64, _, leveling = ai.generate_full_worksheet(
            grade,
            reading_level,
            interests,
            focus_vocabulary=focus_vocabulary,
            focus_phonics=focus_phonics,
            dok_level=dok_levels[0],
            question_count=question_count,
            dok_levels=dok_levels,
        )
        if not is_pro:
            cache.mark_worksheet_used(teacher_id)

        generation_id = _stash_or_none(teacher_id, pdf_bytes, {
            "original": True,
            "sheettype": "worksheet",
            "title": (model_data.get("title") or "Worksheet")[:255],
            "expectedLevel": expected_level,
            "trueLevel": _measured_level(leveling["final"]["estimated_grade"]),
            "targetDOKLevel": model_data["dok_level"],
            "dokMix": (
                None
                if len(dok_levels) == 1
                else {str(level): 1 for level in dok_levels} | {"count": question_count}
            ),
            "phonics": _phonics_value(focus_phonics),
            "informational": False,
            "interest": (interests or "")[:255],
            "studentid": student_id,
            "vocabListid": vocab_list_id,
            "sourceurl": None,
            "content": json.dumps({
                "title": model_data.get("title"),
                "story": model_data.get("story"),
                "questions": model_data.get("questions"),
                "grade": grade,
                "reading_level": reading_level,
                "dok_level": model_data["dok_level"],
                "dok_levels": dok_levels,
                "question_count": question_count,
                "phonics": _phonics_value(focus_phonics),
                "focus_vocabulary": (focus_vocabulary or "").strip(),
            }),
        })

        return jsonify({
            "content": model_data,
            "image_base64": image_base64,
            "pdf_base64": base64.b64encode(pdf_bytes).decode(),
            "reading_level": leveling,
            "generation_id": generation_id,
            "ram_mb": ai.process_ram_mb(),
        })
    except Exception as exc:
        return _json_error(exc, log="Worksheet generation failed")


@app.route("/create-pdf", methods=["POST"])
def create_pdf():
    body = request.get_json() or {}

    try:
        ai.validate_structure(body)
        pdf_bytes, _image = ai.build_pdf_bytes(body, grade=body.get("grade"))
        pdf_data = ai.normalize_model_output(body)
        pdf_path = save_worksheet_pdf(pdf_bytes, pdf_data["Title"])

        return send_file(
            pdf_path,
            mimetype="application/pdf",
            as_attachment=False,
            download_name=f"{pdf_data['Title']}.pdf",
        )
    except Exception as exc:
        return _json_error(exc, log="PDF create failed")


def _restash_edited_generation(body, pdf_bytes):
    """Edits produce a new PDF, so they get a new generation handle."""
    generation_id = (body.get("generation_id") or "").strip()
    teacher = _verified_teacher()
    if teacher is None or not GENERATION_ID_PATTERN.fullmatch(generation_id):
        return None
    try:
        pending = cache.load_pending_worksheet(generation_id)
    except Exception:
        app.logger.exception("Could not read the pending worksheet")
        return None
    if pending is None or pending["teacher_id"] != teacher["teacher_id"]:
        return None
    record = dict(pending["record"])
    content = json.loads(record.get("content") or "{}")
    content.update({
        "title": body.get("title"),
        "story": body.get("story"),
        "questions": body.get("questions"),
    })
    record["title"] = (body.get("title") or record["title"])[:255]
    record["content"] = json.dumps(content)
    return _stash_or_none(teacher["teacher_id"], pdf_bytes, record)


@app.route("/update-pdf", methods=["POST"])
def update_pdf():
    body = request.get_json() or {}

    try:
        ai.validate_structure(body)
        image_feedback = (body.get("image_feedback") or "").strip()
        existing_image = (body.get("image_base64") or "").strip()

        pdf_bytes, image_base64 = ai.build_pdf_bytes(
            body,
            image_feedback=image_feedback or None,
            existing_image_base64=existing_image or None,
            grade=body.get("grade"),
        )

        return jsonify({
            "content": body,
            "image_base64": image_base64,
            "pdf_base64": base64.b64encode(pdf_bytes).decode(),
            "generation_id": _restash_edited_generation(body, pdf_bytes),
            "ram_mb": ai.process_ram_mb(),
        })
    except Exception as exc:
        hitting_flux = bool((body.get("image_feedback") or "").strip()) or not bool(
            (body.get("image_base64") or "").strip()
        )
        return _json_error(
            exc,
            resource="flux" if hitting_flux else "foundry",
            log="PDF update failed",
        )


@app.route("/worksheets/download/<generation_id>", methods=["POST"])
def download_worksheet(generation_id):
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    if not GENERATION_ID_PATTERN.fullmatch(generation_id):
        return jsonify({"error": "Worksheet not found."}), 404
    try:
        sheet, pdf_bytes = _save_generation(teacher_id, generation_id)
    except LookupError:
        return jsonify({
            "error": "This worksheet is no longer available. Generate it again to download.",
        }), 404
    except Exception as exc:
        return _json_error(
            exc,
            resource="storage",
            status=503,
            log="Worksheet save failed",
        )
    response = _pdf_response(pdf_bytes, sheet["title"], as_attachment=True)
    response.headers["X-Worksheet-Id"] = str(sheet["sheetid"])
    return response


@app.route("/worksheets/<int:sheet_id>/view")
def view_worksheet(sheet_id):
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    sheet = db.find_worksheet(teacher_id, sheet_id)
    if sheet is None:
        return jsonify({"error": "Worksheet not found."}), 404
    try:
        pdf_bytes = blobStorage.download_pdf(sheet["bloburl"])
    except Exception as exc:
        return _json_error(
            exc,
            resource="storage",
            status=503,
            log=f"Worksheet blob read failed for sheet {sheet_id}",
        )
    return _pdf_response(pdf_bytes, sheet["title"], as_attachment=False)


@app.route("/worksheets/history")
def worksheet_history():
    teacher_id, error = _require_teacher_id()
    if error:
        return redirect("/login")
    sheets = [_history_item(sheet) for sheet in db.list_worksheets_for_teacher(teacher_id)]
    return render_template(
        "worksheet_history.html",
        active_page="history",
        worksheets=sheets,
        classes=db.list_classes_for_teacher(teacher_id),
        students=db.list_students_for_teacher(teacher_id),
        rating_min=RATING_MIN,
        rating_max=RATING_MAX,
        feedback_max=FEEDBACK_MAX_CHARS,
    )


@app.route("/api/worksheets/<int:sheet_id>/feedback", methods=["POST"])
def api_worksheet_feedback(sheet_id):
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    body = request.get_json() or {}
    feedback = (body.get("feedback") or "").strip() or None
    if feedback and len(feedback) > FEEDBACK_MAX_CHARS:
        return jsonify({
            "error": f"Keep feedback under {FEEDBACK_MAX_CHARS} characters.",
        }), 400
    raw_rating = body.get("rating")
    rating = None
    if raw_rating not in (None, ""):
        try:
            rating = int(raw_rating)
        except (TypeError, ValueError):
            rating = 0
        if not RATING_MIN <= rating <= RATING_MAX:
            return jsonify({
                "error": f"Rating must be {RATING_MIN} through {RATING_MAX}.",
            }), 400
    sheet = db.update_worksheet_feedback(teacher_id, sheet_id, feedback, rating)
    if sheet is None:
        return jsonify({"error": "Worksheet not found."}), 404
    return jsonify({
        "ok": True,
        "feedback": sheet["feedback"] or "",
        "rating": sheet["rating"],
    })


@app.context_processor
def inject_nav():
    teacher_id = session.get("teacher_id")
    empty = {"nav_recent": [], "nav_classes": []}
    if not teacher_id:
        return empty
    recent = []
    classes = []
    try:
        recent = [_history_item(sheet) for sheet in db.list_worksheets_for_teacher(teacher_id)[:5]]
    except Exception:
        app.logger.exception("Could not load recent worksheets for teacher %s", teacher_id)
    try:
        classes = db.list_classes_for_teacher(teacher_id)
    except Exception:
        app.logger.exception("Could not load classes for teacher %s", teacher_id)
    return {"nav_recent": recent, "nav_classes": classes}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    app.run(host="0.0.0.0", debug=debug, port=port)
