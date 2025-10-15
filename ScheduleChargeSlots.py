import logging
import requests
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from config import (
    AGILE_URL, TIMEZONE, RECOMMENDED_SLOTS,
    BATTERY_KWH, CHARGE_RATE_KW, SLOT_HOURS,
    TARGET_SOC, SIMULATION_MODE
)
from db import init_db, add_schedule

# -----------------------------
# Logging
# -----------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

LOCAL_TZ = ZoneInfo(TIMEZONE)

# -----------------------------
# Agile Tariff Handling
# -----------------------------
def fetch_agile_rates():
    """Fetch Agile rates from the configured endpoint."""
    try:
        resp = requests.get(AGILE_URL, timeout=10)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        logging.error(f"Failed to fetch Agile rates: {e}")
        return []

def parse_rates_to_local(results):
    """Return DataFrame with local naive datetimes in columns start, end, rate."""
    if not results:
        return pd.DataFrame(columns=["start", "end", "rate"])
    df = pd.DataFrame(results)
    df['start'] = pd.to_datetime(df['valid_from'], utc=True).dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    df['end'] = pd.to_datetime(df['valid_to'], utc=True).dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    df['rate'] = df['value_inc_vat']
    return df[['start', 'end', 'rate']].sort_values('start').reset_index(drop=True)

def select_cheapest_upcoming_slots(df, slots_count):
    """
    Select the 'slots_count' cheapest *future* slots (end > now),
    sorted by start time.
    """
    now = datetime.now(LOCAL_TZ).replace(tzinfo=None)
    future = df[df['end'] > now].copy()
    if future.empty or slots_count <= 0:
        return pd.DataFrame()
    cheapest = future.nsmallest(slots_count, 'rate')
    return cheapest.sort_values('start').reset_index(drop=True)

# -----------------------------
# Main Scheduler Logic
# -----------------------------
def main():
    logging.info("Scheduler starting: focusing only on cheapest Agile rate slots.")
    init_db()

    # 1) Fetch agile rates
    results = fetch_agile_rates()
    if not results:
        logging.warning("No Agile rates returned. Exiting scheduler.")
        return

    df = parse_rates_to_local(results)

    # 2) Select cheapest N slots (defined in config as RECOMMENDED_SLOTS)
    slots_count = RECOMMENDED_SLOTS if RECOMMENDED_SLOTS > 0 else 4
    logging.info(f"Selecting {slots_count} cheapest upcoming Agile rate slots...")

    chosen = select_cheapest_upcoming_slots(df, slots_count)
    if chosen.empty:
        logging.warning("No suitable upcoming slots found.")
        return

    # 3) Insert chosen slots into DB
    inserted = 0
    for _, row in chosen.iterrows():
        start_iso = row['start'].isoformat()
        end_iso = row['end'].isoformat()
        ok = add_schedule(start_iso, end_iso, mode='autonomous', price=row['rate'])
        if ok:
            inserted += 1
            logging.info(f"Saved schedule: {start_iso} -> {end_iso} at {row['rate']} p/kWh")
        else:
            logging.info(f"Duplicate schedule ignored: {start_iso} -> {end_iso}")

    logging.info(f"Scheduler finished. New schedules inserted: {inserted}")

if __name__ == "__main__":
    main()
