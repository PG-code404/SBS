import openmeteo_requests
import requests
import logging
import os
import pandas as pd
import json
import time
from datetime import datetime, timedelta, timezone
import src.location
from retry_requests import retry

from config.config import (WEATHER_API_BASEURL, WEATHER_CACHE_TTL,
                           PV_AZIMUTH_ANGLE, PV_TILT_ANGLE,
                           PV_NOMINAL_IRRADIANCE, PV_NOMINAL_WATTAGE,
                           PV_SOTC_WATTAGE, WEATHER_CACHE, PV_NUM_PANELS,
                           PV_DERATING_FACTOR)

# Ensure cache folder exists
os.makedirs(os.path.dirname(WEATHER_CACHE), exist_ok=True)


def get_forecast_for_window(start_ts, end_ts):
    if not os.path.exists(WEATHER_CACHE):
        raise FileNotFoundError(f"Weather cache not found: {WEATHER_CACHE}")

    with open(WEATHER_CACHE, 'r') as f:
        cache = json.load(f)

    df = pd.DataFrame(cache['data'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

    df_window = df[(df['timestamp'] >= start_ts)
                   & (df['timestamp'] < end_ts)].copy()

    if df_window.empty:
        logging.error(
            f"⚠️ No forecast data for schedule window {start_ts} → {end_ts}")
        return df_window

    # PV power calculation
    P_nom = PV_NUM_PANELS * PV_NOMINAL_WATTAGE
    P_max = PV_NUM_PANELS * PV_SOTC_WATTAGE

    df_window['pv_power_kw'] = df_window['global_irradiance'].apply(
        lambda G: min(P_nom * (G / PV_NOMINAL_IRRADIANCE) * PV_DERATING_FACTOR,
                      P_max) / 1000)

    return df_window


def hasEnoughSolar(start_ts, end_ts, target_energy_kwh):
    forecast_df = get_forecast_for_window(start_ts, end_ts)
    if forecast_df.empty:
        return False

    avg_power_kw = forecast_df['pv_power_kw'].mean()
    duration_h = (end_ts - start_ts).total_seconds() / 3600
    energy_kwh = avg_power_kw * duration_h

    return energy_kwh >= target_energy_kwh


def is_cache_valid(cache_path):
    if not os.path.exists(cache_path):
        return False

    try:
        with open(cache_path, 'r') as f:
            cache = json.load(f)
        ts = datetime.fromisoformat(cache["cached_timestamp_utc"].replace(
            "Z", "+00:00"))
    except Exception:
        return False

    return datetime.now(timezone.utc) - ts < timedelta(
        seconds=WEATHER_CACHE_TTL)


def fetch_solar_data(force_refresh=False):
    # ✅ Cache check
    if not force_refresh and is_cache_valid(WEATHER_CACHE):
        logging.info(f"✅ Using cached weather data: {WEATHER_CACHE}")
        with open(WEATHER_CACHE, 'r') as f:
            cached = pd.DataFrame(json.load(f)['data'])
            cached['timestamp'] = pd.to_datetime(cached['timestamp'], utc=True)
            return cached

    # ✅ Fetch fresh data
    logging.info("🔄 Fetching fresh weather forecast...")
    session = retry(requests.Session(), retries=5, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=session)

    params = {
        "latitude": src.location.LATITUDE,
        "longitude": src.location.LONGITUDE,
        "models": "best_match",
        "minutely_15": "global_tilted_irradiance_instant",
        "forecast_days": 1,
        "tilt": PV_TILT_ANGLE,
        "azimuth": PV_AZIMUTH_ANGLE,
        "wind_speed_unit": "mph",
    }

    responses = openmeteo.weather_api(WEATHER_API_BASEURL, params=params)
    response = responses[0]
    minutely_15 = response.Minutely15()
    irradiance = minutely_15.Variables(0).ValuesAsNumpy()

    timestamps = pd.date_range(
        start=pd.to_datetime(minutely_15.Time(), unit="s", utc=True),
        end=pd.to_datetime(minutely_15.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=minutely_15.Interval()),
        inclusive="left")

    df = pd.DataFrame({
        "timestamp": timestamps,
        "global_tilted_irradiance_instant": irradiance
    })

    formatted = format_irradiance_data(df)
    save_to_cache(formatted, WEATHER_CACHE)

    return pd.DataFrame(formatted)


def format_irradiance_data(df):
    return [{
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "global_irradiance": int(round(float(val), 0))
    } for ts, val in zip(df["timestamp"],
                         df["global_tilted_irradiance_instant"])]


def save_to_cache(data, cache_path):
    cache_obj = {
        "cached_timestamp_utc":
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "data":
        data
    }

    with open(cache_path, "w") as f:
        json.dump(cache_obj, f, indent=4)

    logging.info(f"✅ Weather cache saved to {cache_path}")


# ✅ Helper to clear cache manually
def clear_weather_cache():
    if os.path.exists(WEATHER_CACHE):
        os.remove(WEATHER_CACHE)
        logging.info("🧹 Weather cache cleared")


if __name__ == "__main__":
    fetch_solar_data()
