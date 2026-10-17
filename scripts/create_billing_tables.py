"""Ensure the tables from granted.sql exist on an existing database.

Runs CREATE TABLE IF NOT EXISTS for every table in the main schema file.
Does not drop the database and does not load seed_dev.sql.

    PYTHONPATH=. ./venv/bin/python scripts/create_billing_tables.py
"""
import os
import re
import sys

from sqlalchemy import text

import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "granted.sql")


def _create_if_missing_statements():
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
        if re.match(r"CREATE\s+TABLE\b", statement, re.IGNORECASE):
            kept.append(re.sub(
                r"^CREATE\s+TABLE\b",
                "CREATE TABLE IF NOT EXISTS",
                statement,
                count=1,
                flags=re.IGNORECASE,
            ))
    return kept


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
