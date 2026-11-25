#!/usr/bin/env python3
"""Delete stale e2e+@granted.test teachers older than N hours.

Usage:
  python scripts/sweep_e2e_data.py
  python scripts/sweep_e2e_data.py --hours 24 --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sweep(older_than_hours: int = 24, dry_run: bool = False) -> int:
    env_test = ROOT / ".env.test"
    if env_test.exists():
        load_dotenv(env_test, override=True)
    else:
        load_dotenv(ROOT / ".env", override=False)

    db_name = (
        os.getenv("LOCAL_DATABASE_NAME") or os.getenv("MYSQL_DATABASE") or ""
    ).lower()
    if "test" not in db_name and db_name != "granteddb":
        raise SystemExit(
            f"Refusing to sweep: database name {db_name!r} must contain 'test' "
            "or be 'granteddb'."
        )

    import db as app_db
    from tests.e2e.helpers.db import delete_teachers

    with app_db.engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT teacherid
                FROM Teachers
                WHERE email LIKE 'e2e+%@granted.test'
                  AND createdAt < (UTC_TIMESTAMP() - INTERVAL :hours HOUR)
                """
            ),
            {"hours": int(older_than_hours)},
        ).fetchall()
    ids = [int(row[0]) for row in rows]
    print(f"Found {len(ids)} stale e2e teacher(s) older than {older_than_hours}h")
    if dry_run or not ids:
        return len(ids)
    delete_teachers(ids)
    print(f"Deleted {len(ids)} teacher(s)")
    return len(ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sweep(older_than_hours=args.hours, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
