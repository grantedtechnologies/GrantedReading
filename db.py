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
                       email, password, verified, country, state, city, school, district
                FROM Teachers
                WHERE email = :email
                ORDER BY verified DESC, teacherid ASC
                LIMIT 1
                """
            ),
            {"email": email},
        ).mappings().first()
        return dict(row) if row else None


def create_teacher(first_name, last_name, email, password, school, district, country, state, city):
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO Teachers
                      (fname, lname, email, password, verified, country, state, city, school, district)
                    VALUES
                      (:first_name, :last_name, :email, :password, 0,
                       :country, :state, :city, :school, :district)
                    """
                ),
                {
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "password": password,
                    "country": country,
                    "state": state,
                    "city": city,
                    "school": school,
                    "district": district,
                },
            )
            return {
                "teacher_id": result.lastrowid,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "country": country,
                "state": state,
                "city": city,
                "school": school,
                "district": district,
                "verified": 0,
            }
    except IntegrityError as exc:
        raise ValueError("That email is already in use.") from exc


def refresh_unverified_teacher(teacher_id, first_name, last_name, password, school, district, country, state, city):
    """Update a pending signup so the teacher can try verification again."""
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE Teachers
                SET fname = :first_name, lname = :last_name, password = :password,
                    country = :country, state = :state, city = :city,
                    school = :school, district = :district
                WHERE teacherid = :teacher_id AND verified = 0
                """
            ),
            {
                "teacher_id": teacher_id,
                "first_name": first_name,
                "last_name": last_name,
                "password": password,
                "country": country,
                "state": state,
                "city": city,
                "school": school,
                "district": district,
            },
        )
        if result.rowcount == 0:
            raise ValueError("That email is already in use.")
    teacher = find_teacher_by_id(teacher_id)
    if teacher is None:
        raise ValueError("That email is already in use.")
    teacher["password"] = password
    return teacher


def set_teacher_password(teacher_id, password):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE Teachers SET password = :password WHERE teacherid = :teacher_id"),
            {"teacher_id": teacher_id, "password": password},
        )


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
                  c.type AS class_type,
                  c.gradeLevel AS grade_level,
                  c.schoolYear AS school_year,
                  COUNT(s.studentid) AS student_count
                FROM class c
                LEFT JOIN Students s ON s.classid = c.classid
                WHERE c.teacherid = :teacher_id
                GROUP BY c.classid, c.teacherid, c.subject, c.name, c.type, c.gradeLevel, c.schoolYear
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


def create_class(teacher_id, name, subject, grade_level, school_year, class_type):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO class (teacherid, subject, name, `type`, gradeLevel, schoolYear)
                VALUES (:teacher_id, :subject, :name, :class_type, :grade_level, :school_year)
                """
            ),
            {
                "teacher_id": teacher_id,
                "subject": subject,
                "name": name,
                "class_type": class_type,
                "grade_level": grade_level,
                "school_year": school_year,
            },
        )
        return {
            "class_id": result.lastrowid,
            "teacher_id": teacher_id,
            "subject": subject,
            "name": name,
            "class_type": class_type,
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
                  s.`reading-level` AS reading_level,
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
                       email, verified, country, state, city, school, district
                FROM Teachers
                WHERE teacherid = :teacher_id
                """
            ),
            {"teacher_id": teacher_id},
        ).mappings().first()
        return dict(row) if row else None


def update_teacher(teacher_id, first_name, last_name, school, district, country, state, city):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE Teachers
                SET fname = :first_name, lname = :last_name,
                    country = :country, state = :state, city = :city,
                    school = :school, district = :district
                WHERE teacherid = :teacher_id
                """
            ),
            {
                "teacher_id": teacher_id,
                "first_name": first_name,
                "last_name": last_name,
                "country": country,
                "state": state,
                "city": city,
                "school": school,
                "district": district,
            },
        )
    return find_teacher_by_id(teacher_id)


def _district_rows(result):
    return [
        {
            "name": row["name"],
            "city": row["city"],
            "state": row["state"],
            "country": row["country"],
        }
        for row in result.mappings()
    ]


def search_districts(country, state, city, query=""):
    country = (country or "").strip()
    state = (state or "").strip()
    city = (city or "").strip()
    query = (query or "").replace("%", "").replace("_", "").strip()
    if not country or not state or not city:
        return []
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT name, city, state, country
                FROM districts
                WHERE LOWER(country) = LOWER(:country)
                  AND (
                    LOWER(state) = LOWER(:state)
                    OR LOWER(stateCode) = LOWER(:state)
                  )
                  AND (
                    LOWER(city) = LOWER(:city)
                    OR (CHAR_LENGTH(:city) >= 3 AND LOWER(city) LIKE CONCAT(LOWER(:city), '%'))
                  )
                  AND (:query = '' OR LOWER(name) LIKE CONCAT('%', LOWER(:query), '%'))
                ORDER BY CASE WHEN LOWER(city) = LOWER(:city) THEN 0 ELSE 1 END, name
                LIMIT 40
                """
            ),
            {"country": country, "state": state, "city": city, "query": query},
        )
        return _district_rows(result)


def nearby_districts(latitude, longitude, miles=50):
    lat = float(latitude)
    lng = float(longitude)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("That location is not valid.")
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT name, city, state, country,
                       (3959 * ACOS(LEAST(1, GREATEST(-1,
                         COS(RADIANS(:lat)) * COS(RADIANS(latitude))
                         * COS(RADIANS(longitude) - RADIANS(:lng))
                         + SIN(RADIANS(:lat)) * SIN(RADIANS(latitude))
                       )))) AS miles
                FROM districts
                WHERE latitude BETWEEN :lat - 2 AND :lat + 2
                  AND longitude BETWEEN :lng - 2 AND :lng + 2
                HAVING miles <= :miles
                ORDER BY miles
                LIMIT 25
                """
            ),
            {"lat": lat, "lng": lng, "miles": miles},
        )
        return _district_rows(result)


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
                  (classid, fname, lname, `grade-level`, `reading-level`, notes)
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
                    `reading-level` = :reading_level,
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


VERSION_COLUMNS = """
  w.sheetid, w.teacherid, w.studentid, w.vocabListid,
  w.title AS worksheet_title, w.createdAt, w.expectedLevel,
  w.DOKLevel AS targetDOKLevel, w.DOKRequest AS dokRequest, w.sheettype, w.phonics,
  w.informational, w.interest, w.sourceurl, w.original, w.studentGradeAtGeneration,
  v.versionid, v.generationid, v.parentVersionid, v.source, v.title, v.content,
  v.trueLevel, v.DOKMix AS dokMix, v.pdfBlobKey, v.imageBlobKey,
  v.rating, v.feedback, v.createdAt AS versionCreatedAt, v.costUsd,
  s.fname AS student_first_name, s.lname AS student_last_name,
  c.classid AS class_id, c.name AS class_name,
  vl.listName AS vocab_list_name,
  (SELECT GROUP_CONCAT(wd.word ORDER BY wd.wordid SEPARATOR ', ')
     FROM words wd WHERE wd.vocabListid = w.vocabListid) AS vocab_words,
  JSON_UNQUOTE(JSON_EXTRACT(v.content, '$.focus_vocabulary')) AS focus_vocabulary
"""


def _json_field(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _version_row(row):
    if row is None:
        return None
    sheet = dict(row)
    sheet["dokMix"] = _json_field(sheet.get("dokMix"))
    sheet["dokRequest"] = _json_field(sheet.get("dokRequest"))
    sheet["original"] = bool(sheet["original"])
    sheet["informational"] = bool(sheet["informational"])
    sheet["bloburl"] = sheet.get("pdfBlobKey")
    return sheet


def _int_or_none(value):
    if value in (None, ""):
        return None
    return int(value)


def _dok_level_value(record):
    raw = record.get("targetDOKLevel")
    if raw in (None, ""):
        request = record.get("dokRequest")
        if request is not None:
            return "custom"
        return None
    if str(raw).strip().lower() == "custom":
        return "custom"
    return str(int(raw))


def _dok_request_value(record, dok_level):
    request = record.get("dokRequest")
    if dok_level == "custom":
        if request is None:
            mix = record.get("dokMix")
            request = mix if mix is not None else {"custom": True}
        return json.dumps(request)
    return None


def find_worksheet(teacher_id, sheet_id, version_id=None):
    if version_id is not None:
        return find_version_for_teacher(teacher_id, version_id=version_id, sheet_id=sheet_id)
    return find_latest_version(teacher_id, sheet_id)


def find_worksheet_by_generation(teacher_id, generation_id):
    return find_version_for_teacher(teacher_id, generation_id=generation_id)


def find_version_for_teacher(teacher_id, *, generation_id=None, version_id=None, sheet_id=None):
    clauses = ["w.teacherid = :teacher_id"]
    params = {"teacher_id": int(teacher_id)}
    if generation_id is not None:
        clauses.append("v.generationid = :generation_id")
        params["generation_id"] = generation_id
    if version_id is not None:
        clauses.append("v.versionid = :version_id")
        params["version_id"] = int(version_id)
    if sheet_id is not None:
        clauses.append("w.sheetid = :sheet_id")
        params["sheet_id"] = int(sheet_id)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT {VERSION_COLUMNS}
                FROM versions v
                JOIN worksheets w ON w.sheetid = v.sheetid
                LEFT JOIN Students s ON s.studentid = w.studentid
                LEFT JOIN class c ON c.classid = s.classid
                LEFT JOIN vocabList vl ON vl.vocabListid = w.vocabListid
                WHERE {" AND ".join(clauses)}
                ORDER BY v.versionid DESC
                LIMIT 1
                """
            ),
            params,
        ).mappings().first()
    return _version_row(row)


def find_latest_version(teacher_id, sheet_id):
    return find_version_for_teacher(teacher_id, sheet_id=sheet_id)


def latest_downloaded_version(teacher_id, sheet_id):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT {VERSION_COLUMNS}
                FROM downloads d
                JOIN versions v ON v.versionid = d.versionid
                JOIN worksheets w ON w.sheetid = v.sheetid
                LEFT JOIN Students s ON s.studentid = w.studentid
                LEFT JOIN class c ON c.classid = s.classid
                LEFT JOIN vocabList vl ON vl.vocabListid = w.vocabListid
                WHERE w.sheetid = :sheet_id AND w.teacherid = :teacher_id
                ORDER BY d.downloadedAt DESC, d.downloadid DESC
                LIMIT 1
                """
            ),
            {"sheet_id": int(sheet_id), "teacher_id": int(teacher_id)},
        ).mappings().first()
    return _version_row(row)


def list_downloaded_versions(teacher_id, limit=None):
    limit_sql = "LIMIT :limit" if limit is not None else ""
    params = {"teacher_id": int(teacher_id)}
    if limit is not None:
        params["limit"] = int(limit)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT {VERSION_COLUMNS},
                       counts.download_count,
                       counts.last_downloaded
                FROM (
                  SELECT versionid,
                         COUNT(*) AS download_count,
                         MAX(downloadedAt) AS last_downloaded
                  FROM downloads
                  GROUP BY versionid
                ) counts
                JOIN versions v ON v.versionid = counts.versionid
                JOIN worksheets w ON w.sheetid = v.sheetid
                LEFT JOIN Students s ON s.studentid = w.studentid
                LEFT JOIN class c ON c.classid = s.classid
                LEFT JOIN vocabList vl ON vl.vocabListid = w.vocabListid
                WHERE w.teacherid = :teacher_id
                ORDER BY counts.last_downloaded DESC, v.versionid DESC
                {limit_sql}
                """
            ),
            params,
        ).mappings().all()
    items = []
    for row in rows:
        item = _version_row(row)
        item["download_count"] = int(row["download_count"])
        item["last_downloaded"] = row["last_downloaded"]
        items.append(item)
    return items


def list_worksheets_for_teacher(teacher_id):
    return list_downloaded_versions(teacher_id)


def insert_worksheet(teacher_id, generation_id, record, store_pdf=None):
    """Insert a worksheet and version, or a new version of an existing sheet.

    `store_pdf(sheet_id, generation_id)` may upload blobs and return
    (pdf_key, image_key). Keys stay NULL when that callback is omitted.
    A second insert for the same generationid raises IntegrityError.
    """
    with engine.begin() as conn:
        sheet_id, version_id = _insert_worksheet_and_version(
            conn, teacher_id, generation_id, record
        )
        if store_pdf is not None:
            keys = store_pdf(sheet_id, generation_id)
            if keys:
                pdf_key, image_key = keys
                conn.execute(
                    text(
                        """
                        UPDATE versions
                        SET pdfBlobKey = :pdf_key, imageBlobKey = :image_key
                        WHERE versionid = :version_id
                        """
                    ),
                    {
                        "pdf_key": pdf_key,
                        "image_key": image_key,
                        "version_id": version_id,
                    },
                )
                conn.execute(
                    text("UPDATE worksheets SET title = :title WHERE sheetid = :sheet_id"),
                    {"title": record["title"][:255], "sheet_id": sheet_id},
                )
    return find_version_for_teacher(teacher_id, generation_id=generation_id)


def _insert_worksheet_and_version(conn, teacher_id, generation_id, record):
    parent_id = _int_or_none(record.get("parentVersionid"))
    sheet_id = _int_or_none(record.get("sheetid"))
    source = record.get("source") or ("edit" if parent_id or sheet_id else "initial")
    if sheet_id is None and parent_id is not None:
        parent = conn.execute(
            text("SELECT sheetid FROM versions WHERE versionid = :parent_id"),
            {"parent_id": parent_id},
        ).mappings().first()
        if parent is None:
            raise ValueError("That version is not on your account.")
        sheet_id = int(parent["sheetid"])
    if sheet_id is None:
        dok_level = _dok_level_value(record)
        result = conn.execute(
            text(
                """
                INSERT INTO worksheets
                  (teacherid, studentid, vocabListid, title, createdAt,
                   expectedLevel, DOKLevel, DOKRequest, sheettype, phonics,
                   informational, interest, sourceurl, original, studentGradeAtGeneration)
                VALUES
                  (:teacher_id, :student_id, :vocab_list_id, :title, UTC_TIMESTAMP(),
                   :expected_level, :dok_level, :dok_request, :sheettype, :phonics,
                   :informational, :interest, :sourceurl, :original, :student_grade)
                """
            ),
            {
                "teacher_id": int(teacher_id),
                "student_id": _int_or_none(record.get("studentid")),
                "vocab_list_id": _int_or_none(record.get("vocabListid")),
                "title": record["title"],
                "expected_level": int(record["expectedLevel"]),
                "dok_level": dok_level,
                "dok_request": _dok_request_value(record, dok_level),
                "sheettype": record["sheettype"],
                "phonics": (record.get("phonics") or None),
                "informational": 1 if record.get("informational") else 0,
                "interest": record.get("interest") or "",
                "sourceurl": record.get("sourceurl"),
                "original": 1 if record["original"] else 0,
                "student_grade": _int_or_none(record.get("studentGradeAtGeneration")),
            },
        )
        sheet_id = result.lastrowid
    dok_mix = record.get("dokMix")
    result = conn.execute(
        text(
            """
            INSERT INTO versions
              (generationid, sheetid, parentVersionid, source, title, content,
               trueLevel, DOKMix, pdfBlobKey, imageBlobKey, costUsd)
            VALUES
              (:generation_id, :sheet_id, :parent_id, :source, :title, :content,
               :true_level, :dok_mix, NULL, NULL, :cost_usd)
            """
        ),
        {
            "generation_id": generation_id,
            "sheet_id": int(sheet_id),
            "parent_id": parent_id,
            "source": source,
            "title": record["title"],
            "content": record.get("content") or "{}",
            "true_level": int(record["trueLevel"]),
            "dok_mix": json.dumps(dok_mix) if dok_mix is not None else None,
            "cost_usd": record.get("costUsd"),
        },
    )
    return int(sheet_id), int(result.lastrowid)


def set_version_blob_keys(teacher_id, generation_id, pdf_key, image_key=None, title=None):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE versions v
                JOIN worksheets w ON w.sheetid = v.sheetid
                SET v.pdfBlobKey = :pdf_key, v.imageBlobKey = :image_key
                WHERE v.generationid = :generation_id AND w.teacherid = :teacher_id
                """
            ),
            {
                "pdf_key": pdf_key,
                "image_key": image_key,
                "generation_id": generation_id,
                "teacher_id": int(teacher_id),
            },
        )
        if title and result.rowcount > 0:
            conn.execute(
                text(
                    """
                    UPDATE worksheets w
                    JOIN versions v ON v.sheetid = w.sheetid
                    SET w.title = :title
                    WHERE v.generationid = :generation_id AND w.teacherid = :teacher_id
                    """
                ),
                {
                    "title": title[:255],
                    "generation_id": generation_id,
                    "teacher_id": int(teacher_id),
                },
            )
        return result.rowcount > 0


def insert_download(version_id, fmt="pdf"):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO downloads (versionid, downloadedAt, format)
                VALUES (:version_id, UTC_TIMESTAMP(3), :fmt)
                """
            ),
            {"version_id": int(version_id), "fmt": fmt},
        )


def update_version_feedback(teacher_id, version_id, feedback, rating):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE versions v
                JOIN worksheets w ON w.sheetid = v.sheetid
                SET v.feedback = :feedback, v.rating = :rating
                WHERE v.versionid = :version_id AND w.teacherid = :teacher_id
                """
            ),
            {
                "version_id": int(version_id),
                "teacher_id": int(teacher_id),
                "feedback": feedback,
                "rating": rating,
            },
        )
        if result.rowcount == 0:
            return None
    return find_version_for_teacher(teacher_id, version_id=version_id)


def update_worksheet_feedback(teacher_id, sheet_id, feedback, rating, version_id=None):
    if version_id is None:
        latest = latest_downloaded_version(teacher_id, sheet_id) or find_latest_version(
            teacher_id, sheet_id
        )
        if latest is None:
            return None
        version_id = latest["versionid"]
    return update_version_feedback(teacher_id, version_id, feedback, rating)


def paid_pro_exists(teacher_id):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT 1
                FROM subscriptions
                WHERE teacherid = :teacher_id
                  AND stripeSubscriptionId IS NOT NULL
                  AND status IN ('active', 'trialing')
                LIMIT 1
                """
            ),
            {"teacher_id": int(teacher_id)},
        ).first()
        return row is not None


def active_pilot_exists(teacher_id):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT 1
                FROM subscriptions s
                JOIN Pilot p ON p.pilotid = s.pilotid
                WHERE s.teacherid = :teacher_id
                  AND s.pilotid IS NOT NULL
                  AND UTC_TIMESTAMP() BETWEEN p.startTime AND p.endTime
                LIMIT 1
                """
            ),
            {"teacher_id": int(teacher_id)},
        ).first()
        return row is not None


def active_pilot_for_teacher(teacher_id):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT p.pilotid, p.name, p.school, p.code, p.startTime, p.endTime
                FROM subscriptions s
                JOIN Pilot p ON p.pilotid = s.pilotid
                WHERE s.teacherid = :teacher_id
                  AND s.pilotid IS NOT NULL
                  AND UTC_TIMESTAMP() BETWEEN p.startTime AND p.endTime
                ORDER BY p.endTime DESC, p.pilotid DESC
                LIMIT 1
                """
            ),
            {"teacher_id": int(teacher_id)},
        ).mappings().first()
        return dict(row) if row else None


def find_pilot_by_code(code):
    cleaned = (code or "").strip()
    if not cleaned:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT p.pilotid, p.name, p.school, p.code, p.startTime, p.endTime,
                       p.districtid AS district_id, d.name AS district_name,
                       d.state AS district_state, d.stateCode AS district_state_code
                FROM Pilot p
                JOIN districts d ON d.districtid = p.districtid
                WHERE LOWER(p.code) = LOWER(:code)
                LIMIT 1
                """
            ),
            {"code": cleaned},
        ).mappings().first()
        return dict(row) if row else None


def join_pilot(teacher_id, pilot):
    """Attach a teacher to a pilot. Rejoining the same pilot is a no-op."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO subscriptions
                  (teacherid, pilotid, stripeCustomerId, stripeSubscriptionId, priceId,
                   price, status, currentPeriodEnd, createdAt, canceledAt)
                VALUES
                  (:teacher_id, :pilot_id, NULL, NULL, NULL,
                   0, 'active', NULL, UTC_TIMESTAMP(), NULL) AS new
                ON DUPLICATE KEY UPDATE
                  status = 'active'
                """
            ),
            {"teacher_id": int(teacher_id), "pilot_id": int(pilot["pilotid"])},
        )


def create_teacher_with_pilot(first_name, last_name, email, password, school, district, country, state, city, pilot):
    """Create the teacher and the pilot subscription in one transaction."""
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO Teachers
                      (fname, lname, email, password, verified, country, state, city, school, district)
                    VALUES
                      (:first_name, :last_name, :email, :password, 0,
                       :country, :state, :city, :school, :district)
                    """
                ),
                {
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "password": password,
                    "country": country,
                    "state": state,
                    "city": city,
                    "school": school,
                    "district": district,
                },
            )
            teacher_id = result.lastrowid
            conn.execute(
                text(
                    """
                    INSERT INTO subscriptions
                      (teacherid, pilotid, stripeCustomerId, stripeSubscriptionId, priceId,
                       price, status, currentPeriodEnd, createdAt, canceledAt)
                    VALUES
                      (:teacher_id, :pilot_id, NULL, NULL, NULL,
                       0, 'active', NULL, UTC_TIMESTAMP(), NULL)
                    """
                ),
                {"teacher_id": int(teacher_id), "pilot_id": int(pilot["pilotid"])},
            )
    except IntegrityError as exc:
        raise ValueError("That email is already in use.") from exc
    return {
        "teacher_id": teacher_id,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "country": country,
        "state": state,
        "city": city,
        "school": school,
        "district": district,
        "verified": 0,
    }


def refresh_unverified_teacher_with_pilot(teacher_id, first_name, last_name, password, school, district, country, state, city, pilot):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE Teachers
                SET fname = :first_name, lname = :last_name, password = :password,
                    country = :country, state = :state, city = :city,
                    school = :school, district = :district
                WHERE teacherid = :teacher_id AND verified = 0
                """
            ),
            {
                "teacher_id": int(teacher_id),
                "first_name": first_name,
                "last_name": last_name,
                "password": password,
                "country": country,
                "state": state,
                "city": city,
                "school": school,
                "district": district,
            },
        )
        if result.rowcount == 0:
            raise ValueError("That email is already in use.")
        conn.execute(
            text(
                """
                INSERT INTO subscriptions
                  (teacherid, pilotid, stripeCustomerId, stripeSubscriptionId, priceId,
                   price, status, currentPeriodEnd, createdAt, canceledAt)
                VALUES
                  (:teacher_id, :pilot_id, NULL, NULL, NULL,
                   0, 'active', NULL, UTC_TIMESTAMP(), NULL) AS new
                ON DUPLICATE KEY UPDATE
                  status = 'active'
                """
            ),
            {"teacher_id": int(teacher_id), "pilot_id": int(pilot["pilotid"])},
        )
    teacher = find_teacher_by_id(teacher_id)
    if teacher is None:
        raise ValueError("That email is already in use.")
    teacher["password"] = password
    return teacher


def latest_subscription(teacher_id):
    """Latest Stripe-backed row, for the portal. Pilot rows are skipped."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT subid, teacherid AS teacher_id,
                       stripeCustomerId AS stripe_customer_id,
                       stripeSubscriptionId AS stripe_subscription_id,
                       priceId AS price_id, price, status,
                       currentPeriodEnd AS current_period_end,
                       createdAt AS created_at, canceledAt AS canceled_at
                FROM subscriptions
                WHERE teacherid = :teacher_id
                  AND stripeSubscriptionId IS NOT NULL
                ORDER BY createdAt DESC, subid DESC
                LIMIT 1
                """
            ),
            {"teacher_id": int(teacher_id)},
        ).mappings().first()
        return dict(row) if row else None


def save_subscription(subscription):
    """Insert or update one Stripe subscription. Returns "saved" or "no_row"."""
    with engine.begin() as conn:
        return "saved" if _write_subscription(conn, subscription) else "no_row"


def claim_webhook_event(conn, event_id, event_type):
    """Insert the event id. Returns True the first time, False on a replay."""
    result = conn.execute(
        text(
            """
            INSERT IGNORE INTO processed_webhook_events (eventId, eventType, processedAt)
            VALUES (:event_id, :event_type, UTC_TIMESTAMP())
            """
        ),
        {"event_id": event_id, "event_type": event_type},
    )
    return result.rowcount > 0


def apply_stripe_event(event_id, event_type, subscription):
    """Claim the webhook event and upsert the Stripe row in one transaction."""
    with engine.begin() as conn:
        if not claim_webhook_event(conn, event_id, event_type):
            return "duplicate"
        return "saved" if _write_subscription(conn, subscription) else "no_row"


def _write_subscription(conn, subscription):
    params = {
        "teacher_id": subscription.get("teacher_id"),
        "customer_id": subscription["customer_id"],
        "subscription_id": subscription["subscription_id"],
        "price_id": subscription.get("price_id"),
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
                  (teacherid, pilotid, stripeCustomerId, stripeSubscriptionId, priceId,
                   price, status, currentPeriodEnd, createdAt, canceledAt)
                VALUES
                  (:teacher_id, NULL, :customer_id, :subscription_id, :price_id,
                   :price, :status, :current_period_end, :created_at, :canceled_at) AS new
                ON DUPLICATE KEY UPDATE
                  stripeCustomerId = new.stripeCustomerId,
                  priceId = new.priceId,
                  price = new.price,
                  status = new.status,
                  currentPeriodEnd = new.currentPeriodEnd,
                  canceledAt = new.canceledAt
                """
            ),
            params,
        )
        return True
    result = conn.execute(
        text(
            """
            UPDATE subscriptions
            SET stripeCustomerId = :customer_id, priceId = :price_id, price = :price,
                status = :status, currentPeriodEnd = :current_period_end,
                canceledAt = :canceled_at
            WHERE stripeSubscriptionId = :subscription_id
              AND pilotid IS NULL
            """
        ),
        params,
    )
    return result.rowcount > 0


def create_login_session(session_id, teacher_id, device_type, browser, os_name, app_version):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO sessions
                  (sessionid, teacherid, startedAt, endedAt, deviceType, browser, os, appVersion)
                VALUES
                  (:session_id, :teacher_id, UTC_TIMESTAMP(), UTC_TIMESTAMP(),
                   :device_type, :browser, :os_name, :app_version)
                """
            ),
            {
                "session_id": session_id,
                "teacher_id": int(teacher_id),
                "device_type": device_type,
                "browser": browser,
                "os_name": os_name,
                "app_version": app_version,
            },
        )


def touch_login_session(session_id):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE sessions
                SET endedAt = UTC_TIMESTAMP()
                WHERE sessionid = :session_id
                """
            ),
            {"session_id": session_id},
        )


def end_login_session(session_id):
    if not session_id:
        return
    touch_login_session(session_id)
