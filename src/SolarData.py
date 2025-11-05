import openmeteo_requests
import requests
import logging
import os
import pandas as pd
import json
import time
from config.config import (
    WEATHER_API_BASEURL, TIMEZONE, WEATHER_HOURLY_VARS, 
    WEATHER_CACHE_TTL, PV_AZIMUTH_ANGLE, PV_TILT_ANGLE,
    PV_NOMINAL_IRRADIANCE, PV_NOMINAL_WATTAGE, PV_SOTC_WATTAGE,
    WEATHER_CACHE, PV_NUM_PANELS, PV_DERATING_FACTOR
)
import src.location

import requests_cache
from retry_requests import retry

import pandas as pd
import json
import os
from config.config import WEATHER_CACHE

def get_forecast_for_window(start_ts, end_ts):
    """
    Reads the WEATHER_CACHE and returns a DataFrame for the schedule window
    including expected PV power (kW) using nominal rating, derating, and max output cap.
    
    Args:
        start_ts (pd.Timestamp): start of schedule (UTC)
        end_ts (pd.Timestamp): end of schedule (UTC)
        
    Returns:
        pd.DataFrame: filtered DataFrame with columns ['timestamp', 'global_irradiance', 'pv_power_kw']
    """
    #main()
    
    if not os.path.exists(WEATHER_CACHE):
        raise FileNotFoundError(f"Weather cache not found: {WEATHER_CACHE}")
    
    with open(WEATHER_CACHE, 'r') as f:
        cache = json.load(f)
        logging.info(f"✅ Loaded weather cache from {WEATHER_CACHE}")

    # Load forecast into DataFrame
    df = pd.DataFrame(cache['data'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

    # Filter for schedule window
    df_window = df[(df['timestamp'] >= start_ts) & (df['timestamp'] < end_ts)].copy()
    
    if df_window.empty:
        logging.error(f"⚠️ No forecast data for schedule window {start_ts} → {end_ts}")
        return df_window

    # Compute PV power using STC scaling, derating factor, and max output
    P_nominal_total = PV_NUM_PANELS * PV_NOMINAL_WATTAGE  # W
    P_max_total = PV_NUM_PANELS * PV_SOTC_WATTAGE         # W

    df_window['pv_power_kw'] = df_window['global_irradiance'].apply(
        lambda G: min(P_nominal_total * (G / PV_NOMINAL_IRRADIANCE) * PV_DERATING_FACTOR, P_max_total) / 1000  # kW
    )
    
    return df_window

def hasEnoughSolar(start_ts, end_ts, target_energy_kwh):
    """
    Returns True if forecast solar energy over the schedule window
    can meet the target energy requirement, False otherwise.
    
    Args:
        start_ts (pd.Timestamp): schedule start in UTC
        end_ts (pd.Timestamp): schedule end in UTC
        target_energy_kwh (float): energy required for schedule in kWh
    """
    forecast_df = get_forecast_for_window(start_ts, end_ts)
    
    if forecast_df.empty:
        return False

    avg_power_kw = forecast_df['pv_power_kw'].mean()
    duration_h = (end_ts - start_ts).total_seconds() / 3600
    energy_kwh = avg_power_kw * duration_h

    return energy_kwh >= target_energy_kwh


def fetch_solar_data(force_refresh=False):
    """
    Fetch 15-min global tilted irradiance from Open-Meteo and save to cache.
    If cache exists and force_refresh=False, return cached data instead.
    """
    # 1️⃣ Check cache first
    if not force_refresh and os.path.exists(WEATHER_CACHE):
        try:
            with open(WEATHER_CACHE, 'r') as f:
                cached = pd.DataFrame(json.load(f)['data'])
                cached['timestamp'] = pd.to_datetime(cached['timestamp'], utc=True)
                logging.info(f"✅ Using cached weather data: {WEATHER_CACHE}")
                return cached
        except Exception as e:
            logging.warning(f"⚠️ Failed to read cache, will refresh: {e}")

    # 2️⃣ Setup Open-Meteo client with cache & retry
    cache_session = requests_cache.CachedSession('.cache', expire_after=WEATHER_CACHE_TTL)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=retry_session)

    # 3️⃣ API params
    latitude = src.location.LATITUDE
    longitude = src.location.LONGITUDE
    tilt = PV_TILT_ANGLE
    azimuth = PV_AZIMUTH_ANGLE

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "models": "best_match",
        "minutely_15": "global_tilted_irradiance_instant",
        "forecast_days": 1,
        "tilt": tilt,
        "azimuth": azimuth,
        "wind_speed_unit": "mph",
    }

    # 4️⃣ Call API
    responses = openmeteo.weather_api(WEATHER_API_BASEURL, params=params)
    response = responses[0]
    minutely_15 = response.Minutely15()
    irradiance = minutely_15.Variables(0).ValuesAsNumpy()

    timestamps = pd.date_range(
        start=pd.to_datetime(minutely_15.Time(), unit="s", utc=True),
        end=pd.to_datetime(minutely_15.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=minutely_15.Interval()),
        inclusive="left"
    )

    df = pd.DataFrame({
        "timestamp": timestamps,
        "global_tilted_irradiance_instant": irradiance
    })

    # 5️⃣ Format & save
    formatted = format_irradiance_data(df)
    save_to_cache(formatted, WEATHER_CACHE)
    logging.info(f"✅ Fetched fresh weather data and saved to cache: {WEATHER_CACHE}")

    return pd.DataFrame(formatted)



def format_irradiance_data(df: pd.DataFrame):
    """
    Converts DataFrame to list of dicts with:
    - timestamp in UTC ISO format with +00:00
    - global irradiance rounded to integer
    """
    #print("--- Formatting data ---")

    formatted = [
        {
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S+00:00"),  # UTC format same as executor
            "global_irradiance": int(round(float(val), 0))
        }
        for ts, val in zip(df["timestamp"], df["global_tilted_irradiance_instant"])
    ]

    #print(f"✅ Formatted {len(formatted)} entries")
    return formatted


def save_to_cache(data, cache_path):
    """
    Saves the formatted data to JSON cache file
    with a UTC timestamp in the same +00:00 format.
    """
    #print("--- Saving to cache ---")

    cache_obj = {
        "cached_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "data": data
    }

    try:
        with open(cache_path, "w") as f:
            json.dump(cache_obj, f, indent=4)
        logging.info(f"✅ Weather Cache saved")
    except IOError as e:
        logging.error(f"❌ Cache write error: {e}")


if __name__ == "__main__":
    fetch_solar_data()
