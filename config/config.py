# ============================================
# CONFIGURATION FILE
# ============================================

import os
import json
import requests
from datetime import time
from dotenv import load_dotenv

load_dotenv()

# -----------------------------
# Simulation and Debug Settings
# -----------------------------
SIMULATION_MODE = False
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# -------------------------------------------------------
# 2️⃣ Base project paths
# -------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # points to src/
ROOT_DIR = os.path.dirname(BASE_DIR)                   # one level up
DATA_DIR = os.path.join(ROOT_DIR, "data")
CONFIG_DIR = os.path.join(ROOT_DIR, "config")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

# -------------------------------------------------------
# 3️⃣ Default file paths
# -------------------------------------------------------
DB_PATH = os.path.join(DATA_DIR, "Force_Charging.db")
LOG_PATH = os.path.join(DATA_DIR, "app.log")
USER_CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# -----------------------------
# Database
# -----------------------------


DB_NAMESPACE = "schedules"
DECISIONS_DB_TABLE = "decisions"

# -----------------------------
# Defining Cache folder
# -----------------------------

CONFIG_CACHE = os.path.join(os.path.expanduser("~"), ".pw_cache", "location_cache.json")
os.makedirs(os.path.dirname(CONFIG_CACHE), exist_ok=True)

"""
LOCATION_CACHE = CACHE_DIR / "location_cache.json"
WEATHER_CACHE = CACHE_DIR / "weather_cache.json"
AGILE_CACHE = CACHE_DIR / "agile_cache.json"
"""

# -----------------------------
# URLs
# -----------------------------
NETZERO_URL_TEMPLATE = "https://api.netzero.energy/api/v1/{SITE_ID}/config"
AGILE_URL = "https://api.octopus.energy/v1/products/AGILE-18-02-21/electricity-tariffs/E-1R-AGILE-18-02-21-H/standard-unit-rates/"
WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"
POSTCODE_URL_TEMPLATE = "https://api.postcodes.io/postcodes/{CUST_POSTCODE}"

# -----------------------------
# NetZero API Configuration
# -----------------------------
NETZERO_API_KEY = os.getenv("NETZERO_API_KEY", "")
SITE_ID = os.getenv("SITE_ID", "")

# Battery reserve & thresholds
BATTERY_RESERVE_START = int(os.getenv("BATTERY_RESERVE_START", 80))
BATTERY_RESERVE_END = int(os.getenv("BATTERY_RESERVE_END", 20))
SOC_SKIP_THRESHOLD = int(os.getenv("SOC_SKIP_THRESHOLD", 90))

# -----------------------------
# Scheduler / Executor Timing
# -----------------------------
SCHEDULE_LOOKAHEAD_HOURS = int(os.getenv("SCHEDULE_LOOKAHEAD_HOURS", 24))
SCHEDULE_INTERVAL_MINUTES = int(os.getenv("SCHEDULE_INTERVAL_MINUTES", 30))
SCHEDULE_POLL_INTERVAL = int(os.getenv("SCHEDULE_POLL_INTERVAL", 15))
FUTURE_SCHEDULE_SLEEP = int(os.getenv("FUTURE_SCHEDULE_SLEEP", 60))
GRACE_RETRY_INTERVAL = int(os.getenv("GRACE_RETRY_INTERVAL", 300))
MAX_AGILE_PRICE_PPK = 22
SCHEDULER_RUNS_PER_DAY=3
KEEP_ALIVE_API_KEY = os.getenv("KEEP_ALIVE_API_KEY", "default-dev-key")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "default-dev-key")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "default-dev-key")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change_me")

# Interval to log active charging status (seconds)
EXECUTOR_RUNNING_STATUS_INTERVAL = int(os.getenv("EXECUTOR_RUNNING_STATUS_INTERVAL", "300"))

# Max number of missed SoC updates before flagging "stuck" charging
MAX_STUCK_SOC_CYCLES = int(os.getenv("MAX_STUCK_SOC_CYCLES", "2"))

# -----------------------------
# Time Windows
# -----------------------------
PEAK_START = time(int(os.getenv("PEAK_START_HOUR", 16)), 0)
PEAK_END = time(int(os.getenv("PEAK_END_HOUR", 19)), 0)
SUNSET_HOUR = int(os.getenv("SUNSET_HOUR", 18))

# -----------------------------
# Solar / Power thresholds
# -----------------------------
SOLAR_POWER_SKIP_W = int(os.getenv("SOLAR_POWER_SKIP_W", 800))



# -----------------------------
# Weather settings
# -----------------------------
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
WEATHER_CACHE_FILE = os.getenv("WEATHER_CACHE_FILE", "weather_cache.json")
WEATHER_CACHE_EXPIRY_HOURS = int(os.getenv("WEATHER_CACHE_EXPIRY_HOURS", 6))
MIN_SOLAR_EXPECTED_TODAY = float(os.getenv("MIN_SOLAR_EXPECTED_TODAY", 2.0))
WEATHER_HOURLY_VARS = ["temperature_2m", "cloud_cover"]
WEATHER_CACHE_TTL = int(os.getenv("WEATHER_CACHE_TTL", "3600"))
TIMEZONE = "Europe/London"  # Default, will be dynamically resolved below

# -----------------------------
# Weather thresholds
# -----------------------------
CLOUD_MAX = int(os.getenv("CLOUD_MAX", "60"))
TARGET_SOC = int(os.getenv("TARGET_SOC", "95"))
MIN_SOC = int(os.getenv("MIN_SOC", "20"))
BATTERY_KWH = float(os.getenv("BATTERY_KWH", "13.5"))
CHARGE_RATE_KW = float(os.getenv("CHARGE_RATE_KW", "5"))

# -----------------------------
# Agile API
# -----------------------------
OCTOPUS_API_KEY = os.getenv("OCTOPUS_API_KEY", "")
OCTOPUS_PRODUCT_CODE = os.getenv("OCTOPUS_PRODUCT_CODE", "AGILE-FLEX-22-11-25")
AGILE_CACHE_FILE = os.getenv("AGILE_CACHE_FILE", "agile_cache.json")
CHEAP_RATE_THRESHOLD = float(os.getenv("CHEAP_RATE_THRESHOLD", 0.18))

# -----------------------------
# Time Zone
# -----------------------------
# (already dynamically resolved above)
# -----------------------------
# Other settings
# -----------------------------
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
RETRY_INTERVAL_SEC = int(os.getenv("RETRY_INTERVAL_SEC", 300))
EXECUTOR_POLL_INTERVAL = SCHEDULE_POLL_INTERVAL
EXECUTOR_IDLE_SLEEP_SEC = int(os.getenv("EXECUTOR_IDLE_SLEEP_SEC", 300))
EXECUTOR_SLEEP_AHEAD_SEC = FUTURE_SCHEDULE_SLEEP
DECISION_LOGGING = os.getenv("DECISION_LOGGING", "true").lower() == "true"
PURGE_DAYS = int(os.getenv("PURGE_DAYS", 2))
RECOMMENDED_SLOTS = int(os.getenv("RECOMMENDED_SLOTS", "5"))
SLOT_HOURS = float(os.getenv("SLOT_HOURS", "0.5"))

