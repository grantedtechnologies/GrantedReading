import base64
import logging
import os
import re
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file, session

import ai
import cache
import db
import file
from reading_level import ReadingLevelError

load_dotenv(override=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "granted-dev-secret")

OUTPUT_DIR = "worksheets"

def save_worksheet_pdf(pdf_bytes, title):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_title = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_") or "worksheet"
    filename = f"{safe_title}_{int(time.time())}.pdf"
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "wb") as f:
        f.write(pdf_bytes)
    return path


def _teacher_session(teacher):
    session["teacher_id"] = teacher["teacher_id"]
    session["teacher_name"] = f"{teacher['first_name']} {teacher['last_name']}"
    session["membership"] = teacher.get("membership") or "standard"


def _require_teacher_id():
    teacher_id = session.get("teacher_id")
    if not teacher_id:
        return None, (jsonify({"error": "Sign in to continue."}), 401)
    return teacher_id, None


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
    return render_template("login.html", initial_mode="signup")


@app.route("/worksheet")
def worksheet():
    return render_template("index.html", active_page="worksheet")


@app.route("/api/students")
def api_students():
    teacher_id = session.get("teacher_id")
    if not teacher_id:
        return jsonify({"error": "Sign in to load students.", "students": []}), 401
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

    _teacher_session(teacher)
    return jsonify({"ok": True, "redirect": "/worksheet"})


@app.route("/signup", methods=["POST"])
def signup():
    body = request.get_json() or {}
    first_name = (body.get("first_name") or "").strip()
    last_name = (body.get("last_name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not first_name or not last_name:
        return jsonify({"error": "Enter a first and last name."}), 400
    if not email or not password:
        return jsonify({"error": "Enter an email and password."}), 400

    membership = (body.get("membership") or "standard").strip().lower()
    if membership not in ("standard", "pro"):
        return jsonify({"error": "Choose Standard or Pro."}), 400

    try:
        teacher = db.create_teacher(
            first_name, last_name, email, password, membership=membership
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409

    _teacher_session(teacher)
    return jsonify({"ok": True, "redirect": "/worksheet"})


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/account")
def account():
    if not session.get("teacher_id"):
        return redirect("/login")
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
    return jsonify({
        "teacher": teacher,
        "students": db.list_students_for_teacher(teacher_id),
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
    membership = (body.get("membership") or "standard").strip().lower()
    if membership not in ("standard", "pro"):
        return jsonify({"error": "Choose Standard or Pro."}), 400
    teacher = db.update_teacher(teacher_id, first_name, last_name, membership)
    _teacher_session(teacher)
    return jsonify({"ok": True, "teacher": teacher})


@app.route("/api/students", methods=["POST"])
def api_create_student():
    teacher_id, error = _require_teacher_id()
    if error:
        return error
    body = request.get_json() or {}
    first_name = (body.get("first_name") or "").strip()
    last_name = (body.get("last_name") or "").strip()
    notes = (body.get("notes") or "").strip()
    try:
        classroom_grade = _parse_grade(body.get("classroom_grade"), "classroom grade")
        reading_level = _parse_grade(body.get("reading_level"), "reading level")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not first_name or not last_name:
        return jsonify({"error": "Enter the student's first and last name."}), 400
    student = db.create_student(
        teacher_id,
        first_name,
        last_name,
        classroom_grade,
        reading_level,
        notes=notes,
    )
    return jsonify({"ok": True, "student": student})


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
        return jsonify({"error": "Choose a student for this word list."}), 400
    if not list_name:
        return jsonify({"error": "Enter a list name."}), 400
    if not words:
        return jsonify({"error": "Add at least one word to the list."}), 400
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
        app.logger.exception("Reading level analysis is unavailable")
        return jsonify({"error": str(exc)}), 500

    result = outcome.correction

    return jsonify({
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
    })


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
    except Exception:
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
        app.logger.exception("Worksheet modulation failed")
        return jsonify({"error": str(exc)}), 500
    except Exception:
        app.logger.exception("Worksheet modulation failed")
        return jsonify({"error": "Could not level that worksheet."}), 500

    return jsonify(result)


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


@app.route("/generate", methods=["POST"])
def generate():
    teacher_id = session.get("teacher_id")
    if not teacher_id:
        return jsonify({"error": "Sign in to generate a worksheet."}), 401

    membership = session.get("membership") or "standard"
    if membership != "pro":
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
    dok_level = body.get("dok_level", 1)

    try:
        model_data, pdf_bytes, image_base64, _, leveling = ai.generate_full_worksheet(
            grade,
            reading_level,
            interests,
            focus_vocabulary=focus_vocabulary,
            focus_phonics=focus_phonics,
            dok_level=dok_level,
        )
        save_worksheet_pdf(pdf_bytes, model_data["title"])
        if membership != "pro":
            cache.mark_worksheet_used(teacher_id)

        return jsonify({
            "content": model_data,
            "image_base64": image_base64,
            "pdf_base64": base64.b64encode(pdf_bytes).decode(),
            "reading_level": leveling,
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "raw_model_output": raw_text if "raw_text" in locals() else None,
        }), 500


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
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        save_worksheet_pdf(pdf_bytes, body["title"])

        return jsonify({
            "content": body,
            "image_base64": image_base64,
            "pdf_base64": base64.b64encode(pdf_bytes).decode(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    app.run(host="0.0.0.0", debug=debug, port=port)
