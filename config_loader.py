"""
config_loader.py
Centralized configuration loader for all environment variables, file paths, and settings.
"""

import os
import json
from dotenv import load_dotenv

# -------------------------------------------------------
# 1️⃣ Load environment variables
# -------------------------------------------------------
load_dotenv()  # Load values from .env if it exists

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

# -------------------------------------------------------
# 4️⃣ Load API keys and environment variables
# -------------------------------------------------------
KEEP_ALIVE_API_KEY = os.getenv("KEEP_ALIVE_API_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "supersecret")

# -------------------------------------------------------
# 5️⃣ Load optional JSON config file (e.g., Octopus, NetZero)
# -------------------------------------------------------
DEFAULT_CONFIG = {
    "OCTOPUS_AGILE_URL": "",
    "NETZERO_API_KEY": "",
    "SCHEDULE_FREQUENCY_HOURS": 8,
    "ENABLED": True,
}

def load_user_config():
    """Load user configuration JSON, or create default if missing."""
    if not os.path.exists(USER_CONFIG_PATH):
        save_user_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG

    try:
        with open(USER_CONFIG_PATH, "r") as f:
            data = json.load(f)
            return {**DEFAULT_CONFIG, **data}  # merge with defaults
    except Exception as e:
        print(f"⚠️ Failed to load config: {e}")
        return DEFAULT_CONFIG

def save_user_config(data: dict):
    """Save updated configuration safely."""
    try:
        with open(USER_CONFIG_PATH, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"⚠️ Failed to save config: {e}")

# -------------------------------------------------------
# 6️⃣ Expose all as CONFIG
# -------------------------------------------------------
CONFIG = load_user_config()