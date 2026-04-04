import os
import sys
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

load_dotenv()

DB_HOST  = os.getenv("DB_HOST", "localhost")
DB_PORT  = int(os.getenv("DB_PORT", 5432))
DB_NAME  = os.getenv("DB_NAME", "sensordata")
DB_ADMIN = os.getenv("DB_ADMIN_USER", "postgres")

# ──────────────────────────────────────────────
# Define Schema
# ──────────────────────────────────────────────
# Schemas for mesurements table - rolling average table - and price table with index
SCHEMA = """
    CREATE TABLE IF NOT EXISTS measurements (
        id              SERIAL PRIMARY KEY,
        box_id          TEXT NOT NULL,
        sensor_id       TEXT NOT NULL,
        phenomenon      TEXT,
        unit            TEXT,
        value           DOUBLE PRECISION NOT NULL,
        measured_at     TIMESTAMPTZ NOT NULL,
        inserted_at     TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (sensor_id, measured_at)
    );
    CREATE INDEX IF NOT EXISTS idx_sensor_time
        ON measurements (sensor_id, measured_at DESC);

    CREATE TABLE IF NOT EXISTS averages (
        id              SERIAL PRIMARY KEY,
        sensor_id       TEXT NOT NULL,
        phenomenon      TEXT NOT NULL,
        unit            TEXT,
        avg_value       DOUBLE PRECISION NOT NULL,
        reading_count   INTEGER NOT NULL,
        window_minutes  INTEGER NOT NULL DEFAULT 5,
        calculated_at   TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (sensor_id, phenomenon, window_minutes)  -- one row per sensor+phenomenon
    );
    
CREATE TABLE IF NOT EXISTS prices (
    id              SERIAL PRIMARY KEY,
    box_id          TEXT NOT NULL,
    index_value     DOUBLE PRECISION NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    calculated_at   TIMESTAMPTZ DEFAULT NOW()
    );

CREATE TABLE IF NOT EXISTS boxes (
    box_id      TEXT PRIMARY KEY,
    box_name    TEXT,
    latitude    DOUBLE PRECISION,
    longitude   DOUBLE PRECISION,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
"""
# ──────────────────────────────────────────────


DB_PASSWORD = os.getenv("DB_ADMIN_PASSWORD") or None

def get_conn(dbname="postgres"):
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=dbname, user=DB_ADMIN,
        password=DB_PASSWORD,        # ignored if None
    )

def create_database():
    conn = get_conn()
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
    if not cur.fetchone():
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DB_NAME)))
        print(f"  ✓ Created database '{DB_NAME}'")
    else:
        print(f"  · Database '{DB_NAME}' already exists, skipping.")

    cur.close()
    conn.close()


def apply_schema():
    conn = get_conn(dbname=DB_NAME)
    with conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
    conn.close()
    print("  ✓ Schema applied.")


def main():
    print("\n=== Database Setup ===\n")

    print("1. Creating database...")
    try:
        create_database()
    except psycopg2.OperationalError as e:
        print(f"\n  ✗ Could not connect to Postgres as '{DB_ADMIN}'.")
        print(f"    Make sure PostgreSQL is running.")
        print(f"    Error: {e}")
        sys.exit(1)

    print("\n2. Applying schema...")
    try:
        apply_schema()
    except psycopg2.Error as e:
        print(f"\n  ✗ Schema setup failed: {e}")
        sys.exit(1)

    print("\n=== Setup complete! ===\n")
    print(f"  Database : {DB_NAME}")
    print(f"  Host     : {DB_HOST}:{DB_PORT}")
    print(f"\nYou can now run: python main.py\n")


if __name__ == "__main__":
    main()