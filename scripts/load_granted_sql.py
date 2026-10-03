"""Load granted.sql into an existing MySQL database.

Railway will not allow DROP DATABASE or CREATE DATABASE, so those statements
and USE are skipped. Tables are created in whatever database the connection
already selected.

From your laptop, put the public proxy URL in .env as MYSQL_PUBLIC_URL, then:

    PYTHONPATH=. ./venv/bin/python scripts/load_granted_sql.py

On Railway itself, MYSQL_URL (the private host) is used when MYSQL_PUBLIC_URL
is not set.
"""
import os
import sys
from urllib.parse import unquote, urlparse

import pymysql
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "granted.sql")


def _env(*names, default=""):
    for name in names:
        value = (os.getenv(name) or "").strip().strip("'\"")
        if value:
            return value
    return default


def _connection():
    load_dotenv(os.path.join(ROOT, ".env"), override=True)
    url = _env("MYSQL_PUBLIC_URL", "DATABASE_URL", "MYSQL_URL")
    if url:
        parsed = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))
        database = (parsed.path or "/").lstrip("/") or None
        return pymysql.connect(
            host=parsed.hostname,
            port=parsed.port or 3306,
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            database=database,
            autocommit=True,
            charset="utf8mb4",
            connect_timeout=15,
        )
    return pymysql.connect(
        host=_env("MYSQL_HOST", "MYSQLHOST", default="localhost"),
        port=int(_env("MYSQL_PORT", "MYSQLPORT", default="3306")),
        user=_env("MYSQL_USER", "MYSQLUSER", default="root"),
        password=_env("MYSQL_PASSWORD", "MYSQLPASSWORD", "MYSQL_ROOT_PASSWORD"),
        database=_env("MYSQL_DATABASE", "MYSQLDATABASE", default="granteddb"),
        autocommit=True,
        charset="utf8mb4",
        connect_timeout=15,
    )


def _statements():
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
    kept = []
    for statement in statements:
        head = statement.lstrip().split(None, 1)[0].upper()
        if head in ("DROP", "CREATE", "USE") and statement.upper().split(None, 2)[1].startswith("DATABASE"):
            continue
        if head == "USE":
            continue
        kept.append(statement)
    return kept


def main():
    connection = _connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            database = cursor.fetchone()[0]
            if not database:
                print("The connection has no database selected. Set MYSQL_DATABASE or MYSQL_PUBLIC_URL.")
                return 1
            for statement in _statements():
                cursor.execute(statement)
            cursor.execute("SHOW TABLES")
            tables = [row[0] for row in cursor.fetchall()]
    finally:
        connection.close()
    print(f"Loaded granted.sql into {database}.")
    print("Tables:", ", ".join(tables) or "(none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
