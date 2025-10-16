import logging
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from config import (
    AGILE_URL, TIMEZONE, RECOMMENDED_SLOTS,
    BATTERY_KWH, CHARGE_RATE_KW, SLOT_HOURS,
    TARGET_SOC, SIMULATION_MODE
)
from db import init_db, add_schedule

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOCAL_TZ = ZoneInfo(TIMEZONE)

def fetch_agile_rates():
    try:
        resp = requests.get(AGILE_URL, timeout=10)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        logging.error(f"Failed to fetch Agile rates: {e}")
        return []

def parse_rates_to_local(results):
    if not results:
        return pd.DataFrame(columns=["start", "end", "rate"])
    df = pd.DataFrame(results)
    df["start"] = pd.to_datetime(df["valid_from"], utc=True).dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    df["end"] = pd.to_datetime(df["valid_to"], utc=True).dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    df["rate"] = df["value_inc_vat"]
    return df[["start", "end", "rate"]].sort_values("start").reset_index(drop=True)

def select_cheapest_upcoming_slots(df, slots_count):
    now = datetime.now(LOCAL_TZ).replace(tzinfo=None)
    future = df[df["end"] > now]
    if future.empty:
        return pd.DataFrame()
    return future.nsmallest(slots_count, "rate").sort_values("start").reset_index(drop=True)

def main():
    logging.info("🔄 Scheduler running — selecting cheapest Agile slots...")
    init_db()

    results = fetch_agile_rates()
    if not results:
        logging.warning("⚠️ No Agile rates returned.")
        return

    df = parse_rates_to_local(results)
    slots_count = max(1, RECOMMENDED_SLOTS or 4)
    chosen = select_cheapest_upcoming_slots(df, slots_count)

    if chosen.empty:
        logging.warning("⚠️ No upcoming cheap slots found.")
        return

    inserted = 0
    for _, row in chosen.iterrows():
        if add_schedule(row["start"].isoformat(), row["end"].isoformat(), mode="autonomous", price=row["rate"]):
            inserted += 1
            logging.info(f"✅ Saved: {row['start']} → {row['end']} @ {row['rate']}p")
        else:
            logging.info(f"Duplicate skipped: {row['start']}")

    logging.info(f"Scheduler complete — {inserted} new slots added.")

def generate_schedules():
    """Safe callable entrypoint for Executor"""
    main()

if __name__ == "__main__":
    main()
