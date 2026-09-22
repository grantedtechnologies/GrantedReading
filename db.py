import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

load_dotenv()


def _database_url():
    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL")
    user = quote_plus(os.getenv("MYSQL_USER", "root"))
    password = quote_plus(os.getenv("MYSQL_PASSWORD", ""))
    host = os.getenv("MYSQL_HOST", "localhost")
    port = os.getenv("MYSQL_PORT", "3306")
    name = os.getenv("MYSQL_DATABASE", "granteddb")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"


engine = create_engine(_database_url(), pool_pre_ping=True)


def find_teacher_by_email(email):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT teacher_id, first_name, last_name, email, password, membership
                FROM teachers
                WHERE email = :email
                """
            ),
            {"email": email},
        ).mappings().first()
        return dict(row) if row else None


def create_teacher(first_name, last_name, email, password, membership="standard"):
    plan = membership if membership in ("standard", "pro") else "standard"
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO teachers (first_name, last_name, email, password, membership)
                    VALUES (:first_name, :last_name, :email, :password, :membership)
                    """
                ),
                {
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "password": password,
                    "membership": plan,
                },
            )
            return {
                "teacher_id": result.lastrowid,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "membership": plan,
            }
    except IntegrityError as exc:
        raise ValueError("That email is already in use.") from exc


def list_students_for_teacher(teacher_id):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  s.student_id,
                  s.teacher_id,
                  s.first_name,
                  s.last_name,
                  s.classroom_grade,
                  s.reading_level,
                  s.notes,
                  vl.vocab_list_id,
                  vl.list_name,
                  vl.description,
                  w.word
                FROM students s
                LEFT JOIN vocab_lists vl ON vl.student_id = s.student_id
                LEFT JOIN words w ON w.vocab_list_id = vl.vocab_list_id
                WHERE s.teacher_id = :teacher_id
                ORDER BY s.last_name, s.first_name, vl.vocab_list_id, w.word_id
                """
            ),
            {"teacher_id": teacher_id},
        ).mappings().all()

    students = {}
    for row in rows:
        student = students.setdefault(
            row["student_id"],
            {
                "student_id": row["student_id"],
                "teacher_id": row["teacher_id"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "classroom_grade": row["classroom_grade"],
                "reading_level": row["reading_level"],
                "notes": row["notes"] or "",
                "vocab_lists": {},
            },
        )
        if row["vocab_list_id"] is None:
            continue
        vocab_list = student["vocab_lists"].setdefault(
            row["vocab_list_id"],
            {
                "vocab_list_id": row["vocab_list_id"],
                "list_name": row["list_name"],
                "description": row["description"],
                "words": [],
            },
        )
        if row["word"]:
            vocab_list["words"].append(row["word"])

    return [
        {**student, "vocab_lists": list(student["vocab_lists"].values())}
        for student in students.values()
    ]


def find_teacher_by_id(teacher_id):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT teacher_id, first_name, last_name, email, membership
                FROM teachers
                WHERE teacher_id = :teacher_id
                """
            ),
            {"teacher_id": teacher_id},
        ).mappings().first()
        return dict(row) if row else None


def update_teacher(teacher_id, first_name, last_name, membership):
    plan = membership if membership in ("standard", "pro") else "standard"
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE teachers
                SET first_name = :first_name, last_name = :last_name, membership = :membership
                WHERE teacher_id = :teacher_id
                """
            ),
            {
                "teacher_id": teacher_id,
                "first_name": first_name,
                "last_name": last_name,
                "membership": plan,
            },
        )
    return find_teacher_by_id(teacher_id)


def create_student(
    teacher_id, first_name, last_name, classroom_grade, reading_level, notes=""
):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO students
                  (teacher_id, first_name, last_name, classroom_grade, reading_level, notes)
                VALUES
                  (:teacher_id, :first_name, :last_name, :classroom_grade, :reading_level, :notes)
                """
            ),
            {
                "teacher_id": teacher_id,
                "first_name": first_name,
                "last_name": last_name,
                "classroom_grade": classroom_grade,
                "reading_level": reading_level,
                "notes": notes or None,
            },
        )
        return {
            "student_id": result.lastrowid,
            "teacher_id": teacher_id,
            "first_name": first_name,
            "last_name": last_name,
            "classroom_grade": classroom_grade,
            "reading_level": reading_level,
            "notes": notes or "",
            "vocab_lists": [],
        }


def _student_owned_by(conn, teacher_id, student_id):
    row = conn.execute(
        text(
            """
            SELECT student_id
            FROM students
            WHERE student_id = :student_id AND teacher_id = :teacher_id
            """
        ),
        {"student_id": student_id, "teacher_id": teacher_id},
    ).mappings().first()
    if row is None:
        raise ValueError("That student is not on your account.")
    return dict(row)


def _list_owned_by(conn, teacher_id, vocab_list_id):
    row = conn.execute(
        text(
            """
            SELECT vl.vocab_list_id
            FROM vocab_lists vl
            JOIN students s ON s.student_id = vl.student_id
            WHERE vl.vocab_list_id = :vocab_list_id AND s.teacher_id = :teacher_id
            """
        ),
        {"vocab_list_id": vocab_list_id, "teacher_id": teacher_id},
    ).mappings().first()
    if row is None:
        raise ValueError("That word list is not on your account.")
    return dict(row)


def _insert_words(conn, vocab_list_id, words):
    added = []
    for word in words:
        conn.execute(
            text(
                "INSERT INTO words (vocab_list_id, word) VALUES (:vocab_list_id, :word)"
            ),
            {"vocab_list_id": vocab_list_id, "word": word},
        )
        added.append(word)
    return added


def create_vocab_list(teacher_id, student_id, list_name, description, words):
    with engine.begin() as conn:
        _student_owned_by(conn, teacher_id, student_id)
        result = conn.execute(
            text(
                """
                INSERT INTO vocab_lists (student_id, list_name, description)
                VALUES (:student_id, :list_name, :description)
                """
            ),
            {
                "student_id": student_id,
                "list_name": list_name,
                "description": description or None,
            },
        )
        vocab_list_id = result.lastrowid
        added = _insert_words(conn, vocab_list_id, words)
        return {
            "vocab_list_id": vocab_list_id,
            "list_name": list_name,
            "description": description or "",
            "words": added,
        }


def add_words_to_list(teacher_id, vocab_list_id, words):
    with engine.begin() as conn:
        _list_owned_by(conn, teacher_id, vocab_list_id)
        return _insert_words(conn, vocab_list_id, words)
