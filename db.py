import json
import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

load_dotenv()


def _env(*names, default=""):
    for name in names:
        value = (os.getenv(name) or "").strip().strip("'\"")
        if value:
            return value
    return default


def _on_railway():
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("RAILWAY_PROJECT_ID")
        or os.getenv("RAILWAY_SERVICE_ID")
    )


def _database_url():
    # Railway sets RAILWAY_ENVIRONMENT on the deployed service. That process
    # uses the private host. A laptop does not, so it uses the local database.
    # MYSQL_USER and MYSQL_PORT are shared. Older MYSQL_HOST / MYSQL_PASSWORD /
    # MYSQL_DATABASE names still work when the matching pair above is unset.
    if _on_railway():
        host = _env("RAILWAY_DATABASE_HOST", "MYSQL_HOST", "MYSQLHOST", default="mysql.railway.internal")
        password = _env("RAILWAY_DATABASE_PASSWORD", "MYSQL_PASSWORD", "MYSQLPASSWORD", "MYSQL_ROOT_PASSWORD")
        name = _env("RAILWAY_DATABASE_NAME", "MYSQL_DATABASE", "MYSQLDATABASE", default="railway")
    else:
        host = _env("LOCAL_DATABASE_HOST", "MYSQL_HOST", "MYSQLHOST", default="localhost")
        password = _env("LOCAL_DATABASE_PASSWORD", "MYSQL_PASSWORD", "MYSQLPASSWORD", "MYSQL_ROOT_PASSWORD")
        name = _env("LOCAL_DATABASE_NAME", "MYSQL_DATABASE", "MYSQLDATABASE", default="granteddb")
    user = quote_plus(_env("MYSQL_USER", "MYSQLUSER", default="root"))
    port = _env("MYSQL_PORT", "MYSQLPORT", default="3306")
    return f"mysql+pymysql://{user}:{quote_plus(password)}@{host}:{port}/{name}"


engine = create_engine(_database_url(), pool_pre_ping=True)


def find_teacher_by_email(email):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT teacherid AS teacher_id, fname AS first_name, lname AS last_name,
                       email, password, verified
                FROM Teachers
                WHERE email = :email
                ORDER BY verified DESC, teacherid ASC
                LIMIT 1
                """
            ),
            {"email": email},
        ).mappings().first()
        return dict(row) if row else None


def create_teacher(first_name, last_name, email, password):
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO Teachers (fname, lname, email, password, verified)
                    VALUES (:first_name, :last_name, :email, :password, 0)
                    """
                ),
                {
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "password": password,
                },
            )
            return {
                "teacher_id": result.lastrowid,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "verified": 0,
            }
    except IntegrityError as exc:
        raise ValueError("That email is already in use.") from exc


def refresh_unverified_teacher(teacher_id, first_name, last_name, password):
    """Update a pending signup so the teacher can try verification again."""
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE Teachers
                SET fname = :first_name, lname = :last_name, password = :password
                WHERE teacherid = :teacher_id AND verified = 0
                """
            ),
            {
                "teacher_id": teacher_id,
                "first_name": first_name,
                "last_name": last_name,
                "password": password,
            },
        )
        if result.rowcount == 0:
            raise ValueError("That email is already in use.")
    teacher = find_teacher_by_id(teacher_id)
    if teacher is None:
        raise ValueError("That email is already in use.")
    teacher["password"] = password
    return teacher


def mark_teacher_verified(teacher_id):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE Teachers SET verified = 1 WHERE teacherid = :teacher_id"),
            {"teacher_id": teacher_id},
        )


def list_classes_for_teacher(teacher_id):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  c.classid AS class_id,
                  c.teacherid AS teacher_id,
                  c.subject,
                  c.name,
                  c.gradeLevel AS grade_level,
                  c.schoolYear AS school_year,
                  COUNT(s.studentid) AS student_count
                FROM class c
                LEFT JOIN Students s ON s.classid = c.classid
                WHERE c.teacherid = :teacher_id
                GROUP BY c.classid, c.teacherid, c.subject, c.name, c.gradeLevel, c.schoolYear
                ORDER BY c.name, c.classid
                """
            ),
            {"teacher_id": teacher_id},
        ).mappings().all()
    classes = []
    for row in rows:
        item = dict(row)
        item["student_count"] = int(item["student_count"])
        classes.append(item)
    return classes


def create_class(teacher_id, name, subject, grade_level, school_year):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO class (teacherid, subject, name, gradeLevel, schoolYear)
                VALUES (:teacher_id, :subject, :name, :grade_level, :school_year)
                """
            ),
            {
                "teacher_id": teacher_id,
                "subject": subject,
                "name": name,
                "grade_level": grade_level,
                "school_year": school_year,
            },
        )
        return {
            "class_id": result.lastrowid,
            "teacher_id": teacher_id,
            "subject": subject,
            "name": name,
            "grade_level": grade_level,
            "school_year": school_year,
            "student_count": 0,
        }


def delete_class(teacher_id, class_id):
    with engine.begin() as conn:
        owned = conn.execute(
            text(
                """
                SELECT classid FROM class
                WHERE classid = :class_id AND teacherid = :teacher_id
                """
            ),
            {"class_id": class_id, "teacher_id": teacher_id},
        ).first()
        if owned is None:
            raise ValueError("That class is not on your account.")
        students = conn.execute(
            text("SELECT 1 FROM Students WHERE classid = :class_id LIMIT 1"),
            {"class_id": class_id},
        ).first()
        if students is not None:
            raise ValueError("Remove the students in this class before removing the class.")
        conn.execute(
            text("DELETE FROM class WHERE classid = :class_id AND teacherid = :teacher_id"),
            {"class_id": class_id, "teacher_id": teacher_id},
        )


def list_students_for_teacher(teacher_id):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  s.studentid AS student_id,
                  c.teacherid AS teacher_id,
                  s.classid AS class_id,
                  c.name AS class_name,
                  c.subject AS class_subject,
                  s.fname AS first_name,
                  s.lname AS last_name,
                  s.`grade-level` AS classroom_grade,
                  s.`roading-level` AS reading_level,
                  s.notes,
                  vl.vocabListid AS vocab_list_id,
                  vl.listName AS list_name,
                  vl.description,
                  w.word
                FROM Students s
                JOIN class c ON c.classid = s.classid
                LEFT JOIN vocabList vl ON vl.studentid = s.studentid
                LEFT JOIN words w ON w.vocabListid = vl.vocabListid
                WHERE c.teacherid = :teacher_id
                ORDER BY s.lname, s.fname, vl.vocabListid, w.wordid
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
                "class_id": row["class_id"],
                "class_name": row["class_name"],
                "class_subject": row["class_subject"],
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
                SELECT teacherid AS teacher_id, fname AS first_name, lname AS last_name,
                       email, verified
                FROM Teachers
                WHERE teacherid = :teacher_id
                """
            ),
            {"teacher_id": teacher_id},
        ).mappings().first()
        return dict(row) if row else None


def update_teacher(teacher_id, first_name, last_name):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE Teachers
                SET fname = :first_name, lname = :last_name
                WHERE teacherid = :teacher_id
                """
            ),
            {
                "teacher_id": teacher_id,
                "first_name": first_name,
                "last_name": last_name,
            },
        )
    return find_teacher_by_id(teacher_id)


def create_student(
    teacher_id, class_id, first_name, last_name, classroom_grade, reading_level, notes=""
):
    with engine.begin() as conn:
        classroom = conn.execute(
            text(
                """
                SELECT classid AS class_id, name
                FROM class
                WHERE classid = :class_id AND teacherid = :teacher_id
                """
            ),
            {"class_id": class_id, "teacher_id": teacher_id},
        ).mappings().first()
        if classroom is None:
            raise ValueError("Choose a class on your account.")
        result = conn.execute(
            text(
                """
                INSERT INTO Students
                  (classid, fname, lname, `grade-level`, `roading-level`, notes)
                VALUES
                  (:class_id, :first_name, :last_name, :classroom_grade, :reading_level, :notes)
                """
            ),
            {
                "class_id": class_id,
                "first_name": first_name,
                "last_name": last_name,
                "classroom_grade": classroom_grade,
                "reading_level": reading_level,
                "notes": notes or "",
            },
        )
        return {
            "student_id": result.lastrowid,
            "teacher_id": teacher_id,
            "class_id": class_id,
            "class_name": classroom["name"],
            "first_name": first_name,
            "last_name": last_name,
            "classroom_grade": classroom_grade,
            "reading_level": reading_level,
            "notes": notes or "",
        }


def find_class_for_teacher(teacher_id, class_id):
    for classroom in list_classes_for_teacher(teacher_id):
        if int(classroom["class_id"]) == int(class_id):
            return classroom
    return None


def list_students_for_class(teacher_id, class_id):
    return [
        student
        for student in list_students_for_teacher(teacher_id)
        if int(student["class_id"]) == int(class_id)
    ]


def find_student_for_teacher(teacher_id, student_id):
    for student in list_students_for_teacher(teacher_id):
        if int(student["student_id"]) == int(student_id):
            return student
    return None


def update_student(
    teacher_id, student_id, first_name, last_name, classroom_grade, reading_level, notes=""
):
    with engine.begin() as conn:
        _student_owned_by(conn, teacher_id, student_id)
        conn.execute(
            text(
                """
                UPDATE Students
                SET fname = :first_name,
                    lname = :last_name,
                    `grade-level` = :classroom_grade,
                    `roading-level` = :reading_level,
                    notes = :notes
                WHERE studentid = :student_id
                """
            ),
            {
                "student_id": student_id,
                "first_name": first_name,
                "last_name": last_name,
                "classroom_grade": classroom_grade,
                "reading_level": reading_level,
                "notes": notes or "",
            },
        )
    student = find_student_for_teacher(teacher_id, student_id)
    if student is None:
        raise ValueError("That student is not on your account.")
    return student


def delete_student(teacher_id, student_id):
    with engine.begin() as conn:
        _student_owned_by(conn, teacher_id, student_id)
        list_ids = [
            row[0]
            for row in conn.execute(
                text("SELECT vocabListid FROM vocabList WHERE studentid = :student_id"),
                {"student_id": student_id},
            ).all()
        ]
        for vocab_list_id in list_ids:
            conn.execute(
                text("DELETE FROM words WHERE vocabListid = :vocab_list_id"),
                {"vocab_list_id": vocab_list_id},
            )
        conn.execute(
            text("DELETE FROM vocabList WHERE studentid = :student_id"),
            {"student_id": student_id},
        )
        conn.execute(
            text("UPDATE worksheets SET studentid = NULL WHERE studentid = :student_id"),
            {"student_id": student_id},
        )
        conn.execute(
            text("DELETE FROM Students WHERE studentid = :student_id"),
            {"student_id": student_id},
        )


def _student_owned_by(conn, teacher_id, student_id):
    row = conn.execute(
        text(
            """
            SELECT s.studentid AS student_id
            FROM Students s
            JOIN class c ON c.classid = s.classid
            WHERE s.studentid = :student_id AND c.teacherid = :teacher_id
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
            SELECT vl.vocabListid AS vocab_list_id
            FROM vocabList vl
            JOIN Students s ON s.studentid = vl.studentid
            JOIN class c ON c.classid = s.classid
            WHERE vl.vocabListid = :vocab_list_id AND c.teacherid = :teacher_id
            """
        ),
        {"vocab_list_id": vocab_list_id, "teacher_id": teacher_id},
    ).mappings().first()
    if row is None:
        raise ValueError("That vocab is not on your account.")
    return dict(row)


def _insert_words(conn, vocab_list_id, words):
    added = []
    for word in words:
        conn.execute(
            text(
                "INSERT INTO words (vocabListid, word) VALUES (:vocab_list_id, :word)"
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
                INSERT INTO vocabList (studentid, listName, description)
                VALUES (:student_id, :list_name, :description)
                """
            ),
            {
                "student_id": student_id,
                "list_name": list_name,
                "description": description or "",
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


def update_vocab_list(teacher_id, vocab_list_id, list_name, description, words):
    with engine.begin() as conn:
        _list_owned_by(conn, teacher_id, vocab_list_id)
        conn.execute(
            text(
                """
                UPDATE vocabList
                SET listName = :list_name, description = :description
                WHERE vocabListid = :vocab_list_id
                """
            ),
            {
                "vocab_list_id": vocab_list_id,
                "list_name": list_name,
                "description": description or "",
            },
        )
        conn.execute(
            text("DELETE FROM words WHERE vocabListid = :vocab_list_id"),
            {"vocab_list_id": vocab_list_id},
        )
        added = _insert_words(conn, vocab_list_id, words)
        return {
            "vocab_list_id": vocab_list_id,
            "list_name": list_name,
            "description": description or "",
            "words": added,
        }


def delete_vocab_list(teacher_id, vocab_list_id):
    with engine.begin() as conn:
        _list_owned_by(conn, teacher_id, vocab_list_id)
        conn.execute(
            text("UPDATE worksheets SET vocabListid = NULL WHERE vocabListid = :vocab_list_id"),
            {"vocab_list_id": vocab_list_id},
        )
        conn.execute(
            text("DELETE FROM words WHERE vocabListid = :vocab_list_id"),
            {"vocab_list_id": vocab_list_id},
        )
        conn.execute(
            text("DELETE FROM vocabList WHERE vocabListid = :vocab_list_id"),
            {"vocab_list_id": vocab_list_id},
        )


def student_belongs_to_teacher(teacher_id, student_id):
    with engine.connect() as conn:
        try:
            _student_owned_by(conn, teacher_id, student_id)
        except ValueError:
            return False
    return True


def vocab_list_belongs_to_teacher(teacher_id, vocab_list_id):
    with engine.connect() as conn:
        try:
            _list_owned_by(conn, teacher_id, vocab_list_id)
        except ValueError:
            return False
    return True


WORKSHEET_COLUMNS = """
  w.sheetid, w.generationid, w.teacherid, w.studentid, w.vocabListid,
  w.title, w.createdAt, w.expectedLevel, w.trueLevel,
  w.DOKLevel AS targetDOKLevel, w.DOKMix AS dokMix, w.sheettype, w.phonics,
  w.informational, w.interest, w.sourceurl, w.bloburl, w.feedback, w.rating, w.original,
  s.fname AS student_first_name, s.lname AS student_last_name,
  c.classid AS class_id, c.name AS class_name,
  vl.listName AS vocab_list_name,
  (SELECT GROUP_CONCAT(wd.word ORDER BY wd.wordid SEPARATOR ', ')
     FROM words wd WHERE wd.vocabListid = w.vocabListid) AS vocab_words,
  JSON_UNQUOTE(JSON_EXTRACT(w.content, '$.focus_vocabulary')) AS focus_vocabulary
"""


def _worksheet_row(row):
    if row is None:
        return None
    sheet = dict(row)
    if isinstance(sheet.get("dokMix"), str):
        sheet["dokMix"] = json.loads(sheet["dokMix"])
    sheet["original"] = bool(sheet["original"])
    sheet["informational"] = bool(sheet["informational"])
    return sheet


def find_worksheet(teacher_id, sheet_id):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT {WORKSHEET_COLUMNS}
                FROM worksheets w
                LEFT JOIN Students s ON s.studentid = w.studentid
                LEFT JOIN class c ON c.classid = s.classid
                LEFT JOIN vocabList vl ON vl.vocabListid = w.vocabListid
                WHERE w.sheetid = :sheet_id AND w.teacherid = :teacher_id
                """
            ),
            {"sheet_id": sheet_id, "teacher_id": teacher_id},
        ).mappings().first()
    return _worksheet_row(row)


def find_worksheet_by_generation(teacher_id, generation_id):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT {WORKSHEET_COLUMNS}
                FROM worksheets w
                LEFT JOIN Students s ON s.studentid = w.studentid
                LEFT JOIN class c ON c.classid = s.classid
                LEFT JOIN vocabList vl ON vl.vocabListid = w.vocabListid
                WHERE w.generationid = :generation_id AND w.teacherid = :teacher_id
                """
            ),
            {"generation_id": generation_id, "teacher_id": teacher_id},
        ).mappings().first()
    return _worksheet_row(row)


def list_worksheets_for_teacher(teacher_id):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT {WORKSHEET_COLUMNS}
                FROM worksheets w
                LEFT JOIN Students s ON s.studentid = w.studentid
                LEFT JOIN class c ON c.classid = s.classid
                LEFT JOIN vocabList vl ON vl.vocabListid = w.vocabListid
                WHERE w.teacherid = :teacher_id
                ORDER BY w.createdAt DESC, w.sheetid DESC
                """
            ),
            {"teacher_id": teacher_id},
        ).mappings().all()
    return [_worksheet_row(row) for row in rows]


def insert_worksheet(teacher_id, generation_id, record, store_pdf):
    """Insert the row, then call `store_pdf(sheet_id)` for the blob key.

    Both happen in one transaction, so a failed upload leaves no row.
    A second insert for the same generation raises IntegrityError on the
    generationid unique key.
    """
    dok_mix = record.get("dokMix")
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO worksheets
                  (generationid, teacherid, studentid, vocabListid, title, createdAt,
                   expectedLevel, trueLevel, DOKLevel, DOKMix, sheettype, phonics, content,
                   informational, interest, sourceurl, bloburl, feedback, rating, original)
                VALUES
                  (:generation_id, :teacher_id, :student_id, :vocab_list_id, :title, UTC_TIMESTAMP(),
                   :expected_level, :true_level, :target_dok, :dok_mix, :sheettype, :phonics, :content,
                   :informational, :interest, :sourceurl, '', NULL, NULL, :original)
                """
            ),
            {
                "generation_id": generation_id,
                "teacher_id": teacher_id,
                "student_id": record.get("studentid"),
                "vocab_list_id": record.get("vocabListid"),
                "title": record["title"],
                "expected_level": record["expectedLevel"],
                "true_level": record["trueLevel"],
                "target_dok": record.get("targetDOKLevel"),
                "dok_mix": json.dumps(dok_mix) if dok_mix is not None else None,
                "sheettype": record["sheettype"],
                "phonics": (record.get("phonics") or None),
                "content": record.get("content"),
                "informational": 1 if record.get("informational") else 0,
                "interest": record.get("interest") or "",
                "sourceurl": record.get("sourceurl"),
                "original": 1 if record["original"] else 0,
            },
        )
        sheet_id = result.lastrowid
        key = store_pdf(sheet_id)
        conn.execute(
            text("UPDATE worksheets SET bloburl = :key WHERE sheetid = :sheet_id"),
            {"key": key, "sheet_id": sheet_id},
        )
    return find_worksheet(teacher_id, sheet_id)


def update_worksheet_feedback(teacher_id, sheet_id, feedback, rating):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE worksheets
                SET feedback = :feedback, rating = :rating
                WHERE sheetid = :sheet_id AND teacherid = :teacher_id
                """
            ),
            {
                "sheet_id": sheet_id,
                "teacher_id": teacher_id,
                "feedback": feedback,
                "rating": rating,
            },
        )
        if result.rowcount == 0 and not conn.execute(
            text(
                "SELECT 1 FROM worksheets WHERE sheetid = :sheet_id AND teacherid = :teacher_id"
            ),
            {"sheet_id": sheet_id, "teacher_id": teacher_id},
        ).first():
            return None
    return find_worksheet(teacher_id, sheet_id)


def latest_subscription(teacher_id):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT subid, teacherid AS teacher_id,
                       stripeCustomerId AS stripe_customer_id,
                       stripeSubscriptionId AS stripe_subscription_id,
                       price, status,
                       currentPeriodEnd AS current_period_end,
                       createdAt AS created_at, canceledAt AS canceled_at
                FROM subscriptions
                WHERE teacherid = :teacher_id
                ORDER BY createdAt DESC, subid DESC
                LIMIT 1
                """
            ),
            {"teacher_id": teacher_id},
        ).mappings().first()
        return dict(row) if row else None


def save_subscription(subscription):
    """Insert or update one subscription. Returns "saved" or "no_row".

    Duplicate deliveries are stopped in Redis before this runs. The unique key
    on stripeSubscriptionId still makes a second write of the same subscription
    an update rather than a second row.
    """
    with engine.begin() as conn:
        return "saved" if _write_subscription(conn, subscription) else "no_row"


def _write_subscription(conn, subscription):
    params = {
        "teacher_id": subscription.get("teacher_id"),
        "customer_id": subscription["customer_id"],
        "subscription_id": subscription["subscription_id"],
        "price": subscription["price"],
        "status": subscription["status"],
        "current_period_end": subscription.get("current_period_end"),
        "created_at": subscription["created_at"],
        "canceled_at": subscription.get("canceled_at"),
    }
    teacher_exists = params["teacher_id"] is not None and conn.execute(
        text("SELECT 1 FROM Teachers WHERE teacherid = :teacher_id"),
        {"teacher_id": params["teacher_id"]},
    ).first()
    if teacher_exists:
        conn.execute(
            text(
                """
                INSERT INTO subscriptions
                  (teacherid, stripeCustomerId, stripeSubscriptionId, price, status,
                   currentPeriodEnd, createdAt, canceledAt)
                VALUES
                  (:teacher_id, :customer_id, :subscription_id, :price, :status,
                   :current_period_end, :created_at, :canceled_at) AS new
                ON DUPLICATE KEY UPDATE
                  stripeCustomerId = new.stripeCustomerId,
                  price = new.price,
                  status = new.status,
                  currentPeriodEnd = new.currentPeriodEnd,
                  canceledAt = new.canceledAt
                """
            ),
            params,
        )
        return True
    # Subscription events only name a teacher when Checkout set the metadata.
    # Without one, only a row that checkout.session.completed already made
    # can be updated.
    result = conn.execute(
        text(
            """
            UPDATE subscriptions
            SET stripeCustomerId = :customer_id, price = :price, status = :status,
                currentPeriodEnd = :current_period_end, canceledAt = :canceled_at
            WHERE stripeSubscriptionId = :subscription_id
            """
        ),
        params,
    )
    return result.rowcount > 0
