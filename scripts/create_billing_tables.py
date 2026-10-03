"""Add the billing tables from granted.sql to an existing database.

Runs only the CREATE TABLE IF NOT EXISTS statements, so no other table is
dropped or reseeded. Safe to re-run.

    PYTHONPATH=. ./venv/bin/python scripts/create_billing_tables.py
"""
import os
import sys

from sqlalchemy import text

import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "granted.sql")


def _create_if_missing_statements():
    with open(SCHEMA_PATH, encoding="utf-8") as handle:
        lines = [line for line in handle if not line.strip().startswith("--")]
    statements = [chunk.strip() for chunk in "".join(lines).split(";")]
    return [
        statement for statement in statements
        if statement.upper().startswith("CREATE TABLE IF NOT EXISTS")
    ]


def main():
    statements = _create_if_missing_statements()
    with db.engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
        database = conn.execute(text("SELECT DATABASE()")).scalar()
        tables = [row[0] for row in conn.execute(text("SHOW TABLES"))]
    print(f"Ran {len(statements)} CREATE TABLE IF NOT EXISTS statement(s) on {database}.")
    print("Tables:", ", ".join(tables))
    return 0


if __name__ == "__main__":
    sys.exit(main())
