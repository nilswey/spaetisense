import os
import time
import logging
import requests
import psycopg2
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

BOX_ID        = os.getenv("OPENSENSEMAP_BOX_ID")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", 60))
API_URL       = f"https://api.opensensemap.org/boxes/{BOX_ID}"

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", 5432))
DB_NAME     = os.getenv("DB_NAME", "sensordata")
DB_ADMIN    = os.getenv("DB_ADMIN_USER", "postgres")
DB_PASSWORD = os.getenv("DB_ADMIN_PASSWORD") or None


def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_ADMIN,
        password=DB_PASSWORD,
    )


def fetch_box_data():
    try:
        resp = requests.get(API_URL, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f"API request failed: {e}")
        return None


def insert_measurement(cur, box_id, sensor, value_str, measured_at):
    try:
        value = float(value_str)
    except (TypeError, ValueError):
        log.warning(f"Skipping non-numeric value '{value_str}' for sensor {sensor['_id']}")
        return False

    cur.execute(
        """
        INSERT INTO measurements (box_id, sensor_id, phenomenon, unit, value, measured_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (sensor_id, measured_at) DO NOTHING
        """,
        (
            box_id,
            sensor["_id"],
            sensor.get("title"),
            sensor.get("unit"),
            value,
            measured_at,
        )
    )
    return cur.rowcount > 0


def poll_once():
    data = fetch_box_data()
    if not data:
        return

    box_id  = data.get("_id", BOX_ID)
    sensors = data.get("sensors", [])
    new_rows = 0

    try:
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                for sensor in sensors:
                    last = sensor.get("lastMeasurement")
                    if not last:
                        continue

                    raw_value = last.get("value")
                    raw_time  = last.get("createdAt")

                    if raw_value is None or raw_time is None:
                        continue

                    try:
                        from datetime import datetime
                        measured_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                    except ValueError:
                        log.warning(f"Could not parse timestamp: {raw_time}")
                        continue

                    inserted = insert_measurement(cur, box_id, sensor, raw_value, measured_at)
                    if inserted:
                        new_rows += 1
                        log.info(
                            f"  ✓ {sensor.get('title')} ({sensor.get('unit')}) "
                            f"= {raw_value} @ {measured_at}"
                        )

        conn.close()
        log.info(f"Poll complete — {new_rows} new row(s) inserted across {len(sensors)} sensor(s).")

    except psycopg2.Error as e:
        log.error(f"Database error: {e}")


def main():
    log.info(f"Starting poller for box: {BOX_ID}")
    log.info(f"Polling every {POLL_INTERVAL}s — press Ctrl+C to stop.\n")
    while True:
        poll_once()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()