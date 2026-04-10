# Spätisense

Click here to explore the data that has already been collected:
https://staging.opensensemap.org/explore/vhrqgsr7wo7nmzjx69o0xlqp

SpaetiSense is a real-time environmental monitoring and dynamic Beer pricing
system that collects sensor data from senseBox devices registered on the
openSenseMap platform, stores and aggregates the data in a relational database, and
publishes the results through a web dashboard. The system is designed to
dynamically adjust the price of beers at a kiosk based on ambient environmental
conditions and busyness measured by the sensors. Fascilitating movement to less
busy areas via price incentive.

The following Repository contains the arduino code for the senseBox and the python code for the 
backend and frontend of the dashboard.

The backend consists of three main components:
1. **`setup_db.py`** – Initializes the PostgreSQL database schema for storing sensor data, rolling averages, and price index history.
2. **`main.py`** – Implements the background service that continuously polls the OpenSenseMap API for sensor data, 
calculates rolling averages, and computes a dynamic price index based on configurable parameters.
3. **`app.py`** – A Streamlit web application that visualizes the real-time sensor data, rolling averages, 
and price index on an interactive dashboard.

The Backend follows the following workflow: (supports multiple boxes for all components)
1. The Poller thread fetches sensor data from the OpenSenseMap API at regular intervals (curr: 60sec)
2. The Averager thread calculates rolling 5-minute averages of the fetched data for each sensor phenomenon. 
Only one 1 average is calculated per phenomenon (and per box) and continusiosly updated every polling interval.
3. The Indexer thread computes a dynamic price index based on the current sensor readings and their rolling averages.
Index behaviour can be adjusted at the top of main.py. The index is designed with min/max normalization 
so that the price adjustments are configurable depending on location or season.

- **`BASE_PRICE`** – Minimum beer price (€)
- **`MAX_MARKUP`** – Price increase at maximum
- **`SMOOTHING`** – Index smoothing factor, removes abrupt index changes (0–1)
- **`CURVE`** – Index curve shape
- **`PHENOMENA_CONFIG`** – Sensor phenomenon weights and min/max values for index calculation


## Prerequisites

- **Python 3.10**
- **PostgreSQL**
- **Sensebox**

## Installation & Setup

### 1. Clone the repository and navigate to the project directory

```bash
git clone https://github.com/nilswey/spaetisense.git
```

```bash
cd path/to/project
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```


### 3. Configure environment variables

Create an `.env` file in the project root with PostgreSQL credentials and OpenSenseMap box ID:

```dotenv
DB_HOST=localhost
DB_PORT=5432
DB_NAME=sensordata
DB_ADMIN_USER=postgres
DB_ADMIN_PASSWORD=example_password

# can be adjusted to fit more than one sensor, boxes must be sepetaed by comma no spaces
OPENSENSEMAP_BOX_IDS=vhrqgsr7wo7nmzjx69o0xlqp

POLL_INTERVAL_SECONDS=60

AVERAGE_INTERVAL_SECONDS=60   # polling interval
AVERAGE_WINDOW_MINUTES=5      # rolling window size for average
```

### 4. Set up the database schema

Start PGAdmin 

Run the database setup script to create tables and indices:

```bash
python setup_db.py
```

This will create the necessary tables for storing sensor data, rolling averages, and price index history in your PostgreSQL database.


### 5. Start the background services

Run the main polling and analytics service:

```bash
python main.py
```

This will start three background threads:
- **Poller**: Fetches sensor data from OpenSenseMap API at regular intervals
- **Averager**: Calculates rolling 5-minute averages for each sensor
- **Indexer**: Computes a dynamic price index based on environmental conditions

The service will run continuously and log activity to the console.

### 6. Launch the Streamlit dashboard

In a new terminal window, run:

```bash
streamlit run app.py
```

The dashboard will be available at `http://localhost:8501` and will display:
- Interactive map of sensor locations
- Current environmental readings 
- 5-minute rolling averages
- Dynamic price index history

## Project Structure

- **`app.py`** – Streamlit web dashboard for visualizing sensor data
- **`main.py`** – Background service (poller, averager, indexer)
- **`setup_db.py`** – Database initialization and schema setup
- **`.env`** – Configuration file (database credentials, API settings)
- **`logo_adj.png`** – Logo assets for the dashboard
- **`collect_data.ino`** – Arduino code for senseBox sensor data collection and transmission to OpenSenseMap



## Troubleshooting

- **Database connection error**: Ensure PostgreSQL is running and credentials in `.env` are correct
- **API request failed**: Verify the `OPENSENSEMAP_BOX_ID` in `.env` is valid
- **Streamlit port in use**: Change the port with `streamlit run app.py --server.port 8502`

