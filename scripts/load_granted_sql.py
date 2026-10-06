"""Load granted.sql into an existing MySQL database.

Railway will not allow DROP DATABASE or CREATE DATABASE, so those statements
and USE are skipped. Tables are created in whatever database the connection
already selected.

This script detects Railway environment and uses the appropriate connection
credentials matching db.py.
"""
import os
import sys
import time
from urllib.parse import quote_plus, unquote, urlparse

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


def _on_railway():
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("RAILWAY_PROJECT_ID")
        or os.getenv("RAILWAY_SERVICE_ID")
    )


def _get_connection_params():
    """Get database connection parameters, matching db.py logic."""
    load_dotenv(os.path.join(ROOT, ".env"), override=True)
    
    # Check for Railway environment
    if _on_railway():
        print("✓ Detected Railway environment", flush=True)
        host = _env("RAILWAY_DATABASE_HOST", "MYSQL_HOST", "MYSQLHOST", default="mysql.railway.internal")
        password = _env("RAILWAY_DATABASE_PASSWORD", "MYSQL_PASSWORD", "MYSQLPASSWORD", "MYSQL_ROOT_PASSWORD")
        database = _env("RAILWAY_DATABASE_NAME", "MYSQL_DATABASE", "MYSQLDATABASE", default="railway")
        print(f"  Using Railway host: {host}", flush=True)
    else:
        print("✓ Local environment detected", flush=True)
        host = _env("LOCAL_DATABASE_HOST", "MYSQL_HOST", "MYSQLHOST", default="localhost")
        password = _env("LOCAL_DATABASE_PASSWORD", "MYSQL_PASSWORD", "MYSQLPASSWORD", "MYSQL_ROOT_PASSWORD")
        database = _env("LOCAL_DATABASE_NAME", "MYSQL_DATABASE", "MYSQLDATABASE", default="granteddb")
        print(f"  Using local host: {host}", flush=True)
    
    user = _env("MYSQL_USER", "MYSQLUSER", default="root")
    port = int(_env("MYSQL_PORT", "MYSQLPORT", default="3306"))
    
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
    }


def _connection():
    """Connect to MySQL with retry logic, using Railway-aware parameters."""
    params = _get_connection_params()
    
    max_retries = 10
    retry_delay = 3
    
    for attempt in range(max_retries):
        try:
            conn = pymysql.connect(
                host=params["host"],
                port=params["port"],
                user=params["user"],
                password=params["password"],
                database=params["database"],
                autocommit=True,
                charset="utf8mb4",
                connect_timeout=15,
            )
            print(f"✓ Connected to MySQL on attempt {attempt + 1}", flush=True)
            return conn
        except pymysql.Error as e:
            print(f"✗ Connection attempt {attempt + 1}/{max_retries} failed: {e}", flush=True)
            if attempt < max_retries - 1:
                print(f"  Retrying in {retry_delay} seconds...", flush=True)
                time.sleep(retry_delay)
            else:
                print("✗ Failed to connect to MySQL after all retries", flush=True)
                sys.exit(1)


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
    print("Starting database schema initialization...", flush=True)
    
    try:
        connection = _connection()
    except Exception as e:
        print(f"✗ Failed to connect to database: {e}", flush=True)
        return 1
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            database = cursor.fetchone()[0]
            if not database:
                print("✗ The connection has no database selected. Set MYSQL_DATABASE or MYSQL_PUBLIC_URL.", flush=True)
                return 1
            
            print(f"✓ Connected to database: {database}", flush=True)
            
            # Check if Teachers table already exists
            cursor.execute(f"SHOW TABLES LIKE 'Teachers'")
            if cursor.fetchone():
                print("✓ Tables already exist, skipping initialization", flush=True)
                return 0
            
            print("Loading schema from granted.sql...", flush=True)
            statements = _statements()
            print(f"  Found {len(statements)} SQL statements", flush=True)
            
            for i, statement in enumerate(statements, 1):
                try:
                    cursor.execute(statement)
                    print(f"  [{i}/{len(statements)}] ✓", flush=True)
                except pymysql.Error as e:
                    # Ignore "table already exists" errors
                    if "already exists" in str(e).lower():
                        print(f"  [{i}/{len(statements)}] ℹ Table already exists (skipping)", flush=True)
                    else:
                        print(f"  [{i}/{len(statements)}] ✗ Error: {e}", flush=True)
                        raise
            
            cursor.execute("SHOW TABLES")
            tables = [row[0] for row in cursor.fetchall()]
            print(f"✓ Schema loaded successfully", flush=True)
            print(f"Tables created: {', '.join(tables)}", flush=True)
            return 0
    except Exception as e:
        print(f"✗ Error initializing schema: {e}", flush=True)
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
