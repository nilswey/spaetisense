import os
import time
import logging
import threading
import requests
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────
BOX_ID           = os.getenv("OPENSENSEMAP_BOX_ID")
API_URL          = f"https://api.opensensemap.org/boxes/{BOX_ID}"
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL_SECONDS", 60))
AVERAGE_INTERVAL = int(os.getenv("AVERAGE_INTERVAL_SECONDS", 60))
WINDOW_MINUTES   = int(os.getenv("AVERAGE_WINDOW_MINUTES", 5))

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", 5432))
DB_NAME     = os.getenv("DB_NAME", "sensordata")
DB_ADMIN    = os.getenv("DB_ADMIN_USER", "postgres")
DB_PASSWORD = os.getenv("DB_ADMIN_PASSWORD") or None

# Edits for the price calculation

# ── price config ─────────────────────────────────────
BASE_PRICE  = 2.0   # € minimum price
MAX_MARKUP  = 1.0   # € added at index = 1.0
SMOOTHING   = 0.3    # 0 = no smoothing, 1 = never changes
CURVE       = 1.5    # 1.0 = linear, 2.0 = quadratic, 0.5 = sqrt

# Min/max for normalization for index - values can be adjusted accordingly

PHENOMENA_CONFIG = {
    "Temperatur": {"min": 0,  "max": 35, "weight": 0.5},
    "rel. Luftfeuchte": {"min": 20, "max": 90, "weight": 0.3},
    "PM2.5":      {"min": 0,  "max": 50, "weight": 0.2},
}

""" Sample Config for final sensor Data
PHENOMENA_CONFIG = {
    "Temperatur": {"min": 25,  "max": 40, "weight": 0.2},
    "UV-Index": {"min": 5, "max": 11, "weight": 0.2},
    "Lautstärke": {"min": 50,  "max": 120, "weight": 0.3},
    "PersAnz": {"min": 8, "max": 20, "weight": 0.3},
},

# Temperatur -> am besten Optimum zwischen 18-25 Grad alles darüber index punishment
# UV Index in deutschland sommer typischerweise 5-8 -> alles unter 5 "normal" keine bewertung und dann index punishment mit steigendem UV https://de.wikipedia.org/wiki/UV-Index
# Lautstärke -> 50 normales gespräch ab 120 DB Schmerzen
# Personen Anzahl -> 5-10 "normal" ab dann index punishment


"""

# DB connection


def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_ADMIN,
        password=DB_PASSWORD,
    )


# ── Poller ───────────────────────────────────────────

def fetch_box_data():
    try:
        resp = requests.get(API_URL, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f"[poller] API request failed: {e}")
        return None

# Inserts the data into SQL

def insert_measurement(cur, box_id, sensor, value_str, measured_at):
    try:
        value = float(value_str)
    except (TypeError, ValueError):
        log.warning(f"[poller] Skipping non-numeric value '{value_str}' for sensor {sensor['_id']}")
        return False

    cur.execute(
        """
        INSERT INTO measurements (box_id, sensor_id, phenomenon, unit, value, measured_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (sensor_id, measured_at) DO NOTHING
        """,
        (box_id, sensor["_id"], sensor.get("title"), sensor.get("unit"), value, measured_at)
    )
    return cur.rowcount > 0


def poll_once():
    data = fetch_box_data()
    if not data:
        return

    box_id   = data.get("_id", BOX_ID)
    sensors  = data.get("sensors", [])
    new_rows = 0

    # Parse location before opening DB connection
    loc = data.get("currentLocation", {}).get("coordinates", [])
    lat, lon = None, None
    if len(loc) >= 2:
        lon, lat = loc[0], loc[1]   # GeoJSON is [lon, lat]

    try:
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:

                # Upsert box location
                if lat is not None and lon is not None:
                    cur.execute("""
                        INSERT INTO boxes (box_id, box_name, latitude, longitude, updated_at)
                        VALUES (%s, %s, %s, %s, NOW())
                        ON CONFLICT (box_id) DO UPDATE SET
                            box_name   = EXCLUDED.box_name,
                            latitude   = EXCLUDED.latitude,
                            longitude  = EXCLUDED.longitude,
                            updated_at = NOW()
                    """, (box_id, data.get("name"), lat, lon))

                # Insert measurements
                for sensor in sensors:
                    last = sensor.get("lastMeasurement")
                    if not last:
                        continue

                    raw_value = last.get("value")
                    raw_time  = last.get("createdAt")
                    if raw_value is None or raw_time is None:
                        continue

                    try:
                        measured_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                    except ValueError:
                        log.warning(f"[poller] Could not parse timestamp: {raw_time}")
                        continue

                    if insert_measurement(cur, box_id, sensor, raw_value, measured_at):
                        new_rows += 1
                        log.info(
                            f"  [poller] ✓ {sensor.get('title')} ({sensor.get('unit')}) "
                            f"= {raw_value} @ {measured_at}"
                        )

        conn.close()
        log.info(f"[poller] Poll complete — {new_rows} new row(s) across {len(sensors)} sensor(s).")

    except psycopg2.Error as e:
        log.error(f"[poller] Database error: {e}")


def run_poller():
    log.info(f"[poller] Starting — polling every {POLL_INTERVAL}s.")
    while True:
        poll_once()
        time.sleep(POLL_INTERVAL)


# ── Averager ─────────────────────────────────────────

def calculate_and_store_averages():
    conn = get_db_connection()
    updated = 0

    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    sensor_id,
                    phenomenon,
                    unit,
                    ROUND(AVG(value)::numeric, 4) AS avg_value,
                    COUNT(*) AS reading_count
                FROM measurements
                WHERE measured_at >= NOW() - INTERVAL '1 minute' * %s
                GROUP BY sensor_id, phenomenon, unit
            """, (WINDOW_MINUTES,))

            for sensor_id, phenomenon, unit, avg_value, reading_count in cur.fetchall():
                cur.execute("""
                    INSERT INTO averages
                        (sensor_id, phenomenon, unit, avg_value, reading_count, window_minutes, calculated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (sensor_id, phenomenon, window_minutes) DO UPDATE SET
                        unit          = EXCLUDED.unit,
                        avg_value     = EXCLUDED.avg_value,
                        reading_count = EXCLUDED.reading_count,
                        calculated_at = NOW()
                """, (sensor_id, phenomenon, unit, avg_value, reading_count, WINDOW_MINUTES))
                updated += 1
                log.info(
                    f"  [averager] ✓ {phenomenon} | sensor {sensor_id} "
                    f"→ avg {avg_value} {unit} ({reading_count} reading(s))"
                )

    conn.close()
    log.info(f"[averager] Done — {updated} average(s) updated.")

def run_averager():
    log.info(f"[averager] Starting — {WINDOW_MINUTES} min window, updating every {AVERAGE_INTERVAL}s.")
    while True:
        try:
            calculate_and_store_averages()
        except psycopg2.Error as e:
            log.error(f"[averager] Database error: {e}")
        time.sleep(AVERAGE_INTERVAL)

# ── Index & Price Calculation ─────────────────────────

def calculate_index(averages: list[dict]) -> float:
    total_weight = 0
    weighted_sum = 0

    for row in averages:
        phenomenon = row["phenomenon"]
        if phenomenon not in PHENOMENA_CONFIG:
            continue

        cfg = PHENOMENA_CONFIG[phenomenon]
        # Normalize to 0–1, clamp to valid range
        normalized = (row["avg_value"] - cfg["min"]) / (cfg["max"] - cfg["min"])
        normalized = max(0.0, min(1.0, normalized))

        weighted_sum += normalized * cfg["weight"]
        total_weight += cfg["weight"]

    if total_weight == 0:
        return 0.0

    index = weighted_sum / total_weight
    return round(index ** CURVE, 4)   # apply curve shape


def smooth_index(new_index: float, conn) -> float:
    with conn.cursor() as cur:
        cur.execute("SELECT index_value FROM prices ORDER BY calculated_at DESC LIMIT 1")
        row = cur.fetchone()

    if row is None:
        return new_index   # no history yet, use raw value

    previous = row[0]
    return round(SMOOTHING * previous + (1 - SMOOTHING) * new_index, 4)


def index_to_price(index: float) -> float:
    return round(BASE_PRICE + index * MAX_MARKUP, 2)

def calculate_and_store_price():
    conn = get_db_connection()

    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT phenomenon, avg_value
                FROM averages
                WHERE window_minutes = %s
            """, (WINDOW_MINUTES,))
            averages = [
                {"phenomenon": row[0], "avg_value": row[1]}
                for row in cur.fetchall()
            ]

    if not averages:
        log.info("[indexer] No averages yet, skipping.")
        conn.close()
        return

    raw_index     = calculate_index(averages)
    smooth        = smooth_index(raw_index, conn)
    price         = index_to_price(smooth)

    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO prices (index_value, price)
                VALUES (%s, %s)
            """, (smooth, price))

    conn.close()
    log.info(f"[indexer] index={smooth} → price=€{price}")


def run_indexer():
    log.info("[indexer] Starting.")
    while True:
        try:
            calculate_and_store_price()
        except psycopg2.Error as e:
            log.error(f"[indexer] Database error: {e}")
        time.sleep(AVERAGE_INTERVAL)

# ── Entry point ──────────────────────────────────────

def main():
    threads = [
        threading.Thread(target=run_poller,   daemon=True, name="poller"),
        threading.Thread(target=run_averager, daemon=True, name="averager"),
        threading.Thread(target=run_indexer, daemon=True, name="indexer"),
    ]
    for t in threads:
        t.start()

    log.info("Both poller and averager and indexer running. Press Ctrl+C to stop.\n")

    # Keep main thread alive so Ctrl+C works
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()