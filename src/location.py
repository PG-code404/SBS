# loc.py
import os
import json
import requests
from config.config import POSTCODE_URL_TEMPLATE, LOCATION_CACHE

# -----------------------------
# Customer Location (auto-resolve)
# -----------------------------
CUST_POSTCODE = os.getenv("CUST_POSTCODE", "SN40GJ")


def get_location_details():
    """Resolve latitude, longitude, and timezone automatically from postcode, with caching."""
    if os.path.exists(LOCATION_CACHE):
        try:
            with open(LOCATION_CACHE, "r") as f:
                cached = json.load(f)
                if cached.get("postcode") == CUST_POSTCODE:
                    return cached
        except json.JSONDecodeError:
            pass

    print(f"🌍 Resolving location for postcode: {CUST_POSTCODE} ...")

    try:
        # 1️⃣ Get lat/lon via postcodes.io (UK open data)
        r = requests.get(POSTCODE_URL_TEMPLATE.format(CUST_POSTCODE=CUST_POSTCODE), timeout=10)
        r.raise_for_status()
        result = r.json().get("result", {})
        lat = result.get("latitude")
        lon = result.get("longitude")

        if lat is None or lon is None:
            raise ValueError("Postcode lookup failed.")

        # 2️⃣ Resolve timezone from Open-Meteo
        tz_req = requests.get(
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&timezone=auto",
            timeout=10,
        )
        tz_data = tz_req.json()
        timezone = tz_data.get("timezone", "Europe/London")

        location_info = {
            "postcode": CUST_POSTCODE,
            "latitude": lat,
            "longitude": lon,
            "timezone": timezone,
        }

        with open(LOCATION_CACHE, "w") as f:
            json.dump(location_info, f, indent=2)

        print(f"✅ Location cached: {location_info}")
        return location_info

    except Exception as e:
        print(f"⚠️ Failed to auto-resolve location: {e}. Falling back to manual values.")
        return {
            "postcode": CUST_POSTCODE,
            "latitude": float(os.getenv("LATITUDE", "51.5074")), #Default for London
            "longitude": float(os.getenv("LONGITUDE", "-0.1278")), #Default for London
            "timezone": os.getenv("TIMEZONE", "Europe/London"),
        }


# Load resolved location
LOCATION = get_location_details()
LATITUDE = LOCATION["latitude"]
LONGITUDE = LOCATION["longitude"]
TIMEZONE = LOCATION["timezone"]

if __name__ == "__main__":
    print(f"✅ Location loaded for postcode: {LOCATION['postcode']}, timezone: {LOCATION['timezone']}")
