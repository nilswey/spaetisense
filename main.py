import os
import time
import logging
import threading
import requests
import psycopg2
from datetime import datetime
from dotenv import load_dotenv
import math


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ----- Config ------------

BOX_IDS = [b.strip() for b in os.getenv("OPENSENSEMAP_BOX_IDS", "").split(",") if b.strip()]
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL_SECONDS", 60))
AVERAGE_INTERVAL = int(os.getenv("AVERAGE_INTERVAL_SECONDS", 60))
WINDOW_MINUTES   = int(os.getenv("AVERAGE_WINDOW_MINUTES", 5))

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", 5432))
DB_NAME     = os.getenv("DB_NAME", "sensordata")
DB_ADMIN    = os.getenv("DB_ADMIN_USER", "postgres")
DB_PASSWORD = os.getenv("DB_ADMIN_PASSWORD") or None

# --------Edits for the price calculation----

BASE_PRICE  = 2.0   # € minimum price
MAX_MARKUP  = 1.0   # Max price markup achievable
SMOOTHING   = 0.3    # smoothing to avoid abrupt index changes: 0 = no smoothing, 1 = never changes
CURVE       = 1.0    # multiplication curve, that regulates index growth: 1 = linear, 2 = quadratic

# Min/max for normalization for index - values can be adjusted accordingly

# Value explenations
# Temperatur -> optimal till 20 - 25 degrees, then index punishment with rising temperature
# UV Index in germany typically 5-8 -> alles unter 5 "normal" keine bewertung und dann index punishment mit steigendem UV https://de.wikipedia.org/wiki/UV-Index
# Lautstärke -> 50 normales gespräch ab 120 DB Schmerzen
# Personen Anzahl -> 5-10 "normal" ab dann index punishment

#  Config for final sensor Data, adjustable
PHENOMENA_CONFIG = {
    "Temperature": {"min": 20,  "max": 40, "weight": 0.1},
    "UV": {"min": 5, "max": 11, "weight": 0.1},
    "Sound Level": {"min": 50,  "max": 120, "weight": 0.4},
    "People": {"min": 0, "max": 15, "weight": 0.4},
}


log.info(f"[config] BOX_IDS loaded: {BOX_IDS}")
log.info(f"[config] .env loaded")

# UV values to UV index adjusted from https://www.brunweb.de/veml6070-uv-sensor/

def convert_uv_to_index(raw_value: float) -> float:

    if raw_value <= 560:
        return 1.0    # UV 0-2
    elif raw_value <= 1120:
        return 5.0    # UV 3-5 till normal german UV index
    elif raw_value <= 1494:
        return 6.0    # UV 6-7
    elif raw_value <= 2054:
        return 9.0    # UV 8-10
    elif raw_value <= 9999:
        return 11.0   # UV >10
    else:
        return 0.0    # fallback

# DB connection

def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_ADMIN,
        password=DB_PASSWORD,
    )


# ── Poller ───────────────────────────────────────────

def fetch_box_data(box_id: str):
    try:
        # staging api
        resp = requests.get(f"https://api.staging.opensensemap.org/boxes/{box_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f"[poller] API request failed for {box_id}: {e}")
        return None

# Inserts the data intoDB

def insert_measurement(cur, box_id, sensor, value_str, measured_at):
    sensor_id = sensor.get("_id") or sensor.get("id")
    if not sensor_id:
        log.warning(f"[poller] Skipping sensor with no id: {sensor}")
        return False

    try:
        value = float(value_str)
    except (TypeError, ValueError):
        log.warning(f"[poller] Skipping non-numeric value '{value_str}' for sensor {sensor_id}")
        return False

    # Convert raw UV reading to UV index
    if sensor.get("title") == "UV":
        value = convert_uv_to_index(value)

    cur.execute(
        """
        INSERT INTO measurements (box_id, sensor_id, phenomenon, unit, value, measured_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (sensor_id, measured_at) DO NOTHING
        """,
        (box_id, sensor_id, sensor.get("title"), sensor.get("unit"), value, measured_at)
    )
    return cur.rowcount > 0


def poll_once():
    for box_id in BOX_IDS:
        data = fetch_box_data(box_id)
        if not data:
            continue

        actual_box_id = data.get("_id", box_id)
        sensors       = data.get("sensors", [])
        new_rows      = 0

        lat = data.get("latitude") or data.get("currentLocation", {}).get("coordinates", [None, None, None])[1]
        lon = data.get("longitude") or data.get("currentLocation", {}).get("coordinates", [None, None, None])[0]

        log.info(f"[poller] lat={lat} lon={lon} box_id={actual_box_id} name={data.get('name')}")
        log.info(
            f"[poller] full location field: {data.get('currentLocation')} | loc field: {data.get('loc')} | location: {data.get('location')}")
        try:
            conn = get_db_connection()
            with conn:
                with conn.cursor() as cur:

                    if lat is not None and lon is not None:
                        cur.execute("""
                            INSERT INTO boxes (box_id, box_name, latitude, longitude, updated_at)
                            VALUES (%s, %s, %s, %s, NOW())
                            ON CONFLICT (box_id) DO UPDATE SET
                                box_name   = EXCLUDED.box_name,
                                latitude   = EXCLUDED.latitude,
                                longitude  = EXCLUDED.longitude,
                                updated_at = NOW()
                        """, (actual_box_id, data.get("name"), lat, lon))

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

                        if insert_measurement(cur, actual_box_id, sensor, raw_value, measured_at):
                            new_rows += 1
                            log.info(
                                f"  [poller] ✓ {sensor.get('title')} ({sensor.get('unit')}) "
                                f"= {raw_value} @ {measured_at}"
                            )

            conn.close()
            log.info(f"[poller] {actual_box_id} — {new_rows} new row(s) across {len(sensors)} sensor(s).")

        except psycopg2.Error as e:
            log.error(f"[poller] Database error for {box_id}: {e}")


def run_poller():
    log.info(f"[poller] Starting — polling every {POLL_INTERVAL}s.")
    while True:
        poll_once()
        time.sleep(POLL_INTERVAL)


# ---- Averager -------------

def calculate_and_store_averages():

    updated = 0

    for box_id in BOX_IDS:
        conn = get_db_connection()
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
                    AND box_id = %s
                    GROUP BY sensor_id, phenomenon, unit
                """, (WINDOW_MINUTES, box_id))

                rows = cur.fetchall()

            with conn.cursor() as cur:
                for sensor_id, phenomenon, unit, avg_value, reading_count in rows:

                    # round count of person up to avoid half perosns, because model is leaning to underestimation
                    if phenomenon == "People":
                        avg_value = math.ceil(avg_value)

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

        # Normalize to 0–1 bassed on config
        normalized = (row["avg_value"] - cfg["min"]) / (cfg["max"] - cfg["min"])
        normalized = max(0.0, min(1.0, normalized))

        weighted_sum += normalized * cfg["weight"]
        total_weight += cfg["weight"]

    if total_weight == 0:
        return 0.0

    index = weighted_sum / total_weight
    return round(index ** CURVE, 4)   # apply curve, current linear


def smooth_index(new_index: float, conn, box_id: str) -> float:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT index_value FROM prices
            WHERE box_id = %s
            ORDER BY calculated_at DESC LIMIT 1
        """, (box_id,))
        row = cur.fetchone()

    if row is None:
        return new_index

    previous = row[0]
    return round(SMOOTHING * previous + (1 - SMOOTHING) * new_index, 4)


def index_to_price(index: float) -> float:
    return round(BASE_PRICE + index * MAX_MARKUP, 2)

def calculate_and_store_price():
    conn = get_db_connection()

    for box_id in BOX_IDS:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT phenomenon, avg_value
                    FROM averages
                    WHERE sensor_id IN (
                        SELECT DISTINCT sensor_id FROM measurements WHERE box_id = %s
                    )
                    AND window_minutes = %s
                """, (box_id, WINDOW_MINUTES))
                averages = [
                    {"phenomenon": r[0], "avg_value": r[1]}
                    for r in cur.fetchall()
                ]

        if not averages:
            log.info(f"[indexer] No averages yet for {box_id}, skipping.")
            continue

        raw_index = calculate_index(averages)

        with conn:
            smooth = smooth_index(raw_index, conn, box_id)
            price  = index_to_price(smooth)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO prices (box_id, index_value, price)
                    VALUES (%s, %s, %s)
                """, (box_id, smooth, price))

        log.info(f"[indexer] {box_id} → index={smooth} price=€{price}")

    conn.close()



def run_indexer():
    log.info("[indexer] Starting.")
    while True:
        try:
            calculate_and_store_price()
        except psycopg2.Error as e:
            log.error(f"[indexer] Database error: {e}")
        time.sleep(AVERAGE_INTERVAL)

# ---- Main ---- Run Threads

def main():
    threads = [
        threading.Thread(target=run_poller,   daemon=True, name="poller"),
        threading.Thread(target=run_averager, daemon=True, name="averager"),
        threading.Thread(target=run_indexer, daemon=True, name="indexer"),
    ]
    for t in threads:
        t.start()

    log.info("poller, averager and indexer running\n")

    # Keep main thread alive so Ctrl+C works
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()