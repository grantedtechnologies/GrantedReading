"""Reset the local MySQL database and teacher-attached worksheets in Blob Storage.

Reloads granted.sql (drops granteddb and recreates the tables), then loads
seed_dev.sql. That seed is DEV ONLY: personas, classes, sessions, and
Events for those rows. Worksheets and versions stay empty. Then deletes
blob PDFs stored under a teacher. PDFs sitting at the container root stay.

Do not run this against production. It uses LOCAL_DATABASE_* on a laptop.

    PYTHONPATH=. ./venv/bin/python seed_both.py
"""
import os
import sys

import pymysql
from dotenv import load_dotenv

import blobStorage
import district_directory

ROOT = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(ROOT, "granted.sql")
SEED_PATH = os.path.join(ROOT, "seed_dev.sql")


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


def _sql_statements(path):
    statements = []
    chunk = []
    with open(path, encoding="utf-8") as handle:
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


def _run_sql(cursor, path):
    statements = _sql_statements(path)
    for statement in statements:
        cursor.execute(statement)
    return len(statements)


def reload_schema():
    connection = _server_connection()
    try:
        with connection.cursor() as cursor:
            schema_count = _run_sql(cursor, SCHEMA_PATH)
            district_count = district_directory.load(cursor)
            seed_count = _run_sql(cursor, SEED_PATH)
    finally:
        connection.close()
    return schema_count, seed_count, district_count


def clear_attached_worksheets():
    """Delete teacher-attached blobs and keep everything else."""
    container = blobStorage._container_client()
    removed = []
    kept = 0
    for blob in container.list_blobs():
        name = blob.name
        if name.startswith("teacher_") or name.split("/", 1)[0].isdigit():
            blobStorage.delete_pdf(name)
            removed.append(name)
        else:
            kept += 1
    return removed, kept


def main():
    if _on_railway():
        print("seed_both.py refuses to run when Railway environment variables are set.")
        return 1
    schema_count, seed_count, district_count = reload_schema()
    print(f"Reloaded granted.sql ({schema_count} statements) and seed_dev.sql ({seed_count} statements).")
    print(f"Loaded {district_count} districts.")
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
