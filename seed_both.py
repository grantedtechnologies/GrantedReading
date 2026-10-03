"""Reset MySQL and the teacher-attached worksheets in Blob Storage.

Reloads granted.sql (drops granteddb, recreates the tables, and inserts the
seed teachers, students, and vocab). Then deletes blob PDFs stored under
a teacher (teacher_{id}/...). PDFs sitting at the container root stay.

Run from the project root:

    PYTHONPATH=. ./venv/bin/python seed_both.py
"""
import os
import sys

import pymysql
from dotenv import load_dotenv

import blobStorage

ROOT = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(ROOT, "granted.sql")


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


def _server_connection():
    """Connect without selecting a database, so DROP DATABASE can run.

    Uses the same host and password as the app: LOCAL_DATABASE_* on a laptop,
    RAILWAY_DATABASE_* when Railway sets RAILWAY_ENVIRONMENT.
    """
    load_dotenv(os.path.join(ROOT, ".env"), override=True)
    if _on_railway():
        host = _env("RAILWAY_DATABASE_HOST", "MYSQL_HOST", "MYSQLHOST", default="mysql.railway.internal")
        password = _env("RAILWAY_DATABASE_PASSWORD", "MYSQL_PASSWORD", "MYSQLPASSWORD", "MYSQL_ROOT_PASSWORD")
    else:
        host = _env("LOCAL_DATABASE_HOST", "MYSQL_HOST", "MYSQLHOST", default="localhost")
        password = _env("LOCAL_DATABASE_PASSWORD", "MYSQL_PASSWORD", "MYSQLPASSWORD", "MYSQL_ROOT_PASSWORD")
    return pymysql.connect(
        host=host,
        port=int(_env("MYSQL_PORT", "MYSQLPORT", default="3306")),
        user=_env("MYSQL_USER", "MYSQLUSER", default="root"),
        password=password,
        autocommit=True,
        charset="utf8mb4",
    )


def _schema_statements():
    statements = []
    chunk = []
    with open(SCHEMA_PATH, encoding="utf-8") as handle:
        for line in handle:
            if line.strip().startswith("--"):
                continue
            chunk.append(line)
            if line.strip().endswith(";"):
                statement = "".join(chunk).strip()
                chunk = []
                if statement:
                    statements.append(statement)
    leftover = "".join(chunk).strip()
    if leftover:
        statements.append(leftover)
    return statements


def reload_schema():
    statements = _schema_statements()
    connection = _server_connection()
    try:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
    finally:
        connection.close()
    return len(statements)


def clear_attached_worksheets():
    """Delete blobs named teacher_{id}/... and keep everything else."""
    container = blobStorage._container_client()
    removed = []
    kept = 0
    for blob in container.list_blobs():
        if blob.name.startswith("teacher_"):
            blobStorage.delete_pdf(blob.name)
            removed.append(blob.name)
        else:
            kept += 1
    return removed, kept


def main():
    count = reload_schema()
    print(f"Reloaded granted.sql ({count} statements).")
    removed, kept = clear_attached_worksheets()
    if removed:
        print(f"Removed {len(removed)} teacher-attached worksheet(s):")
        for name in removed:
            print(f"  {name}")
    else:
        print("No teacher-attached worksheets in blob storage.")
    print(f"Kept {kept} worksheet(s) that are not attached to a teacher.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
