import os
import psycopg2
import streamlit as st
import folium
from streamlit_folium import st_folium
from dotenv import load_dotenv

load_dotenv()

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", 5432))
DB_NAME     = os.getenv("DB_NAME", "sensordata")
DB_ADMIN    = os.getenv("DB_ADMIN_USER", "postgres")
DB_PASSWORD = os.getenv("DB_ADMIN_PASSWORD") or None

st.set_page_config(
    page_title="Sensor Dashboard",
    page_icon="🌡️",
    layout="wide"
)

# ── DB ───────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_ADMIN,
        password=DB_PASSWORD,
    )


@st.cache_data(ttl=30)   # refresh every 30s
def load_boxes():
    """Load all unique sensorboxes with their last known location from OpenSenseMap."""
    conn = get_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (box_id)
                    box_id,
                    latitude,
                    longitude,
                    box_name
                FROM boxes
                ORDER BY box_id
            """)
            rows = cur.fetchall()
    conn.close()
    return [
        {"box_id": r[0], "lat": r[1], "lon": r[2], "name": r[3]}
        for r in rows
    ]


@st.cache_data(ttl=30)
def load_averages(box_id: str):
    conn = get_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT phenomenon, unit, avg_value, reading_count, calculated_at
                FROM averages
                WHERE sensor_id IN (
                    SELECT DISTINCT sensor_id FROM measurements WHERE box_id = %s
                )
                ORDER BY phenomenon
            """, (box_id,))
            rows = cur.fetchall()
    conn.close()
    return [
        {
            "phenomenon":    r[0],
            "unit":          r[1],
            "avg_value":     r[2],
            "reading_count": r[3],
            "calculated_at": r[4],
        }
        for r in rows
    ]


@st.cache_data(ttl=30)
def load_latest_price():
    conn = get_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT price, index_value, calculated_at
                FROM prices
                ORDER BY calculated_at DESC
                LIMIT 1
            """)
            row = cur.fetchone()
    conn.close()
    if row:
        return {"price": row[0], "index": row[1], "calculated_at": row[2]}
    return None


@st.cache_data(ttl=30)
def load_recent_measurements(box_id: str, limit: int = 10):
    conn = get_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT phenomenon, unit, value, measured_at
                FROM measurements
                WHERE box_id = %s
                ORDER BY measured_at DESC
                LIMIT %s
            """, (box_id, limit))
            rows = cur.fetchall()
    conn.close()
    return [
        {
            "phenomenon": r[0],
            "unit":       r[1],
            "value":      r[2],
            "measured_at": r[3],
        }
        for r in rows
    ]

@st.cache_data(ttl=30)
def load_price_history(limit: int = 10):
    conn = get_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT price, calculated_at
                FROM prices
                ORDER BY calculated_at DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
    conn.close()
    return [{"price": r[0], "calculated_at": r[1]} for r in reversed(rows)]

# ── Map ──────────────────────────────────────────────

def build_map(boxes: list, selected_box_id: str | None):
    if boxes:
        center = [boxes[0]["lat"], boxes[0]["lon"]]
    else:
        center = [51.96, 7.62]   # Münster fallback

    m = folium.Map(location=center, zoom_start=13, tiles="CartoDB positron")

    for box in boxes:
        is_selected = box["box_id"] == selected_box_id
        folium.CircleMarker(
            location=[box["lat"], box["lon"]],
            radius=10 if is_selected else 7,
            color="#1D9E75" if is_selected else "#888780",
            fill=True,
            fill_color="#1D9E75" if is_selected else "#B4B2A9",
            fill_opacity=0.9 if is_selected else 0.6,
            tooltip=box["name"] or box["box_id"],
            popup=folium.Popup(box["box_id"], parse_html=True),
        ).add_to(m)

    return m


# ── UI ───────────────────────────────────────────────

st.image("logo_adj.png", width=450)
st.caption("Live sensor readings, 5-minute averages and current index price.")

boxes = load_boxes()

# Fallback if boxes table not yet populated
if not boxes:
    st.info("No sensorbox location data yet. Add a `boxes` table entry or wait for data.")
    st.stop()

col_map, col_detail = st.columns([3, 2], gap="large")

with col_map:
    st.subheader("Sensor locations")

    if "selected_box" not in st.session_state:
        st.session_state.selected_box = boxes[0]["box_id"] if boxes else None

    selected_id = st.session_state.selected_box
    m = build_map(boxes, selected_id)
    map_output = st_folium(m, height=420, width=None, returned_objects=["last_object_clicked_popup"])

    # Update selection when user clicks a marker
    clicked = map_output.get("last_object_clicked_popup")
    if clicked and clicked != selected_id:
        st.session_state.selected_box = clicked
        st.rerun()

    # Also allow selection via dropdown (useful with many boxes)
    box_names  = {b["box_id"]: (b["name"] or b["box_id"]) for b in boxes}
    chosen = st.selectbox(
        "Or select a box",
        options=list(box_names.keys()),
        format_func=lambda x: box_names[x],
        index=list(box_names.keys()).index(st.session_state.selected_box)
              if st.session_state.selected_box in box_names else 0,
        label_visibility="collapsed",
    )
    if chosen != st.session_state.selected_box:
        st.session_state.selected_box = chosen
        st.rerun()


with col_detail:
    box_id = st.session_state.selected_box
    box_info = next((b for b in boxes if b["box_id"] == box_id), None)

    st.subheader(box_info["name"] if box_info and box_info["name"] else box_id)

    # ── Current price ────────────────────────────────
    price_data = load_latest_price()
    if price_data:
        st.markdown("#### 🍺 Current beer price")
        st.metric("Price", f"€ {price_data['price']:.2f}")
        st.caption(f"Last calculated: {price_data['calculated_at'].strftime('%H:%M:%S')}")

        # Line chart
        history = load_price_history()
        if len(history) > 1:
            import pandas as pd
            df = pd.DataFrame(history)
            df["calculated_at"] = pd.to_datetime(df["calculated_at"], utc=True)
            st.line_chart(
                df.set_index("calculated_at")["price"],
                color="#1D9E75",
            )
        else:
            st.caption("Not enough data for a chart yet.")
    else:
        st.info("No price data yet.")

    st.divider()

    # ── 5-minute averages ────────────────────────────
    st.markdown("#### 5-minute averages")
    averages = load_averages(box_id)

    if averages:
        cols = st.columns(2)
        for i, avg in enumerate(averages):
            cols[i % 2].metric(
                label=f"{avg['phenomenon']} ({avg['unit']})",
                value=f"{avg['avg_value']:.2f}",
                help=f"{avg['reading_count']} readings · updated {avg['calculated_at'].strftime('%H:%M:%S')}"
            )
    else:
        st.info("No averages yet — poller may still be collecting data.")

    st.divider()

    # ── Recent raw readings ──────────────────────────
    with st.expander("Recent raw readings"):
        measurements = load_recent_measurements(box_id)
        if measurements:
            for m in measurements:
                st.text(
                    f"{m['measured_at'].strftime('%H:%M:%S')}  "
                    f"{m['phenomenon']}: {m['value']} {m['unit']}"
                )
        else:
            st.info("No measurements yet.")

    if st.button("↻ Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()