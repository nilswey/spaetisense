# Spätisense – Real-time Sensor Dashboard

Spätisense is a real-time sensor monitoring and analytics platform that integrates with the OpenSenseMap API to collect environmental sensor data and stores it in a PostgreSQL database. The system continuously polls sensor readings, calculates rolling averages, computes a dynamic price index based on environmental conditions, and provides a live interactive dashboard built with Streamlit for visualization and analysis.

## Prerequisites

- **Python 3.8+**
- **PostgreSQL** (running locally or remotely)
- **pip** (Python package manager)

## Installation & Setup

### 1. Clone the repository and navigate to the project directory

```bash
cd C:\Skripte\Spaetisense
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

If you don't have a `requirements.txt` yet, install the required packages manually:

```bash
pip install psycopg2-binary streamlit folium streamlit-folium python-dotenv requests pandas
```

### 3. Configure environment variables

Create or update the `.env` file in the project root with your PostgreSQL credentials and OpenSenseMap box ID:

```dotenv
DB_HOST=localhost
DB_PORT=5432
DB_NAME=sensordata
DB_ADMIN_USER=postgres
DB_ADMIN_PASSWORD=your_password_here

OPENSENSEMAP_BOX_ID=your_box_id_here
POLL_INTERVAL_SECONDS=60
AVERAGE_INTERVAL_SECONDS=60
AVERAGE_WINDOW_MINUTES=5
```

### 4. Set up the database schema

Run the database setup script to create tables and indices:

```bash
python setup_db.py
```

This will:
- Create the `sensordata` database (if it doesn't exist)
- Create the following tables: `measurements`, `averages`, `prices`, and `boxes`
- Create necessary indices for performance

### 5. Start the background services

Run the main polling and analytics service:

```bash
python main.py
```

This will start three background threads:
- **Poller**: Fetches sensor data from OpenSenseMap API at regular intervals
- **Averager**: Calculates rolling 5-minute averages for each sensor
- **Indexer**: Computes a dynamic price index based on environmental conditions

The service will run continuously and log activity to the console. Press `Ctrl+C` to stop.

### 6. Launch the Streamlit dashboard

In a new terminal window, run:

```bash
streamlit run app.py
```

The dashboard will be available at `http://localhost:8501` and will display:
- Interactive map of sensor locations
- Current environmental readings (temperature, humidity, PM2.5)
- 5-minute rolling averages
- Dynamic price index history

## Project Structure

- **`app.py`** – Streamlit web dashboard for visualizing sensor data
- **`main.py`** – Background service (poller, averager, indexer)
- **`setup_db.py`** – Database initialization and schema setup
- **`.env`** – Configuration file (database credentials, API settings)
- **`logo_*.png`** – Logo assets for the dashboard

## Configuration

Edit the following in `main.py` to customize behavior:

- **`BASE_PRICE`** – Minimum beer price (€)
- **`MAX_MARKUP`** – Price increase at maximum index
- **`SMOOTHING`** – Index smoothing factor (0–1)
- **`CURVE`** – Index curve shape (1.0=linear, 2.0=quadratic)
- **`PHENOMENA_CONFIG`** – Sensor phenomenon weights and min/max values for index calculation

## Troubleshooting

- **Database connection error**: Ensure PostgreSQL is running and credentials in `.env` are correct
- **API request failed**: Verify the `OPENSENSEMAP_BOX_ID` in `.env` is valid
- **Streamlit port in use**: Change the port with `streamlit run app.py --server.port 8502`

