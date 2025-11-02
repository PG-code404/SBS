import requests
import logging
import pandas as pd
import time
from config.config import WEATHER_API_URL, LATITUDE, LONGITUDE, TIMEZONE, WEATHER_HOURLY_VARS, WEATHER_CACHE_TTL

_cached = {"ts": 0, "df": None}


def fetch_weather_forecast():
    """
    Pull hourly data from Open-Meteo (raw). Returns DataFrame with columns:
    - datetime (pd.Timestamp localized to config.TIMEZONE)
    - temperature_2m
    - cloud_cover
    """
    try:
        params = {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "hourly": ",".join(WEATHER_HOURLY_VARS),
            "timezone": TIMEZONE,
        }
        resp = requests.get(WEATHER_API_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            return pd.DataFrame()

        df = pd.DataFrame({
            "datetime": pd.to_datetime(times).tz_localize(None),
            "temperature_2m": hourly.get("temperature_2m", []),
            "cloud_cover": hourly.get("cloud_cover", []),
        })
        return df
    except Exception as e:
        logging.error(f"Weather API error: {e}")
        return pd.DataFrame()


def get_cached_weather():
    """
    Returns cached DataFrame; refreshes when older than WEATHER_CACHE_TTL seconds.
    """
    now = time.time()
    if _cached["df"] is None or now - _cached["ts"] > WEATHER_CACHE_TTL:
        _cached["df"] = fetch_weather_forecast()
        _cached["ts"] = now
    return _cached["df"]
