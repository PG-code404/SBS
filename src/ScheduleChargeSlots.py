import logging
import requests
import threading
import pandas as pd
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from config.config import (
    AGILE_URL, TIMEZONE, RECOMMENDED_SLOTS,
    BATTERY_KWH, CHARGE_RATE_KW, SLOT_HOURS,
    TARGET_SOC, SIMULATION_MODE, BATTERY_RESERVE_START
)

from src.db import add_schedule,add_schedules_batch,add_manual_override
from src.netzero_api import get_battery_status

from src.timezone_utils import to_utc

scheduler_refresh_event = threading.Event()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOCAL_TZ = ZoneInfo(TIMEZONE)

def fetch_agile_rates():
    try:
        resp = requests.get(AGILE_URL, timeout=30)
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

from datetime import datetime, time, timedelta

def add_manual_charge_schedule(start_time_str: str, end_time_str: str, target_soc: int = 98):
    """
    Add a manual schedule for charging.

    date_str: 'YYYY-MM-DD'
    start_time_str: 'HH:MM'
    end_time_str: 'HH:MM'
    target_soc: 0-100
    """
    start_dt = datetime.fromisoformat(start_time_str)
    end_dt = datetime.fromisoformat(end_time_str)

    start_dt_utc = to_utc(start_dt)
    end_dt_utc = to_utc(end_dt)
    # Ensure start < end
    if start_dt >= end_dt:
        raise ValueError("Start time must be before end time")

    # Store in DB
    add_manual_override(start_dt_utc, end_dt_utc, target_soc)
    print(f"✅ Manual schedule added: {start_dt} → {end_dt}, target SOC: {target_soc}%")

    # Trigger scheduler refresh
    scheduler_refresh_event.set()
    print("[Scheduler] Manual schedule added — scheduler refresh triggered.")

def compute_required_kwh(current_soc: float, target_soc: float = TARGET_SOC):
    """
    Returns how many kWh are needed to go from current SoC to target SoC.
    """
    delta_soc = max(0, target_soc - current_soc)
    return (delta_soc / 100.0) * BATTERY_KWH

def compute_required_hours(kwh_needed: float):
    """
    Convert kWh needed into hours based on CHARGE_RATE_KW.
    """
    if CHARGE_RATE_KW <= 0:
        return 0
    return kwh_needed / CHARGE_RATE_KW

def compute_required_slots(hours_needed: float):
    slot_hours = SLOT_HOURS  # from config (e.g. 0.5 hours / 30 mins)
    return max(1, int(hours_needed / slot_hours + 0.999))  # round up

def main():
    logging.info("🔄 Scheduler running — selecting cheapest Agile slots...")
    #init_db()
    
    try:
        soc = get_battery_status()
        current_soc = float(soc.get('percentage_charged', 0)) if soc else 0.0
        kwh_needed = compute_required_kwh(current_soc)
        hours_needed = compute_required_hours(kwh_needed)
        slots_count = compute_required_slots(hours_needed)
        logging.info(f"Schedules - Current SOC {current_soc} KWH {kwh_needed} hours {hours_needed} Slots {slots_count}")
    except Exception as e:
        logging.error(f"⚠️ Failed to auto-compute slots, falling back to RECOMMENDED_SLOTS: {e}")
        slots_count = RECOMMENDED_SLOTS or 5
        logging.info(f"Using fallback slots_count = {slots_count}.")
        
    results = fetch_agile_rates()
    if not results:
        logging.warning("⚠️ No Agile rates returned.")
        return

    df = parse_rates_to_local(results)
    # refactored to automatically select number of charging slots 
    chosen = select_cheapest_upcoming_slots(df, slots_count)
    chosen_sorted = chosen.sort_values("start")

    if chosen.empty:
        logging.warning("⚠️ No upcoming cheap slots found.")
        return

    inserted = 0
    # Prepare all slots for batch insert
    #replaced row["start"].replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc).isoformat() with to_utc()
    schedules = [
        (to_utc(row["start"]),
         to_utc(row["end"]),
         "autonomous", BATTERY_RESERVE_START,row["rate"])
        for _, row in chosen_sorted.iterrows()
    ]

    logging.info(f"Prepared {len(schedules)} schedules for insertion.")

    if schedules:
        inserted = add_schedules_batch(schedules)
        logging.info(f"Scheduler complete — {inserted} new slots added.")
    else:
        logging.info("No valid schedules to insert after sorting.")

def generate_schedules():
    """Safe callable entrypoint for Executor"""
    main()

import time

def scheduler_loop():
    """
    Background loop that waits for new manual or automatic triggers.
    It listens to scheduler_refresh_event and runs main() when triggered.
    """
    time.sleep(10)
    logging.info("🌀 Scheduler background loop started. Waiting for events...")

    while True:
        # Wait for either refresh event or 15-minute default interval
        triggered = scheduler_refresh_event.wait(timeout=900)
        if triggered:
            logging.info("🔁 Refresh event detected — running scheduler now.")
            scheduler_refresh_event.clear()
        else:
            logging.info("⏱️ Timer trigger — running periodic schedule refresh.")

        try:
            main()  # runs your Agile fetch + DB add logic
        except Exception as e:
            logging.error(f"Scheduler loop error: {e}")
            import traceback; traceback.print_exc()
        finally:
            # Wait 1 second before next iteration to avoid tight loops
            time.sleep(5)


if __name__ == "__main__":
    main()
