import logging
import time
import requests
import sys
import signal
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import (
    DB_PATH, DB_NAMESPACE, TIMEZONE,
    BATTERY_RESERVE_START, BATTERY_RESERVE_END,
    SOC_SKIP_THRESHOLD, PEAK_START, PEAK_END,
    SOLAR_POWER_SKIP_W, SIMULATION_MODE,
    EXECUTOR_POLL_INTERVAL, EXECUTOR_SLEEP_AHEAD_SEC,
    EXECUTOR_IDLE_SLEEP_SEC, GRACE_RETRY_INTERVAL,
    AGILE_URL, MAX_AGILE_PRICE_PPK
)
from db import (
    fetch_pending_schedules, mark_as_executed,
    add_decision, get_last_retry, update_last_retry,
    get_stored_price, mark_all_expired
)
from netzero_api import get_battery_status, set_charge

# -----------------------------
# Logging & Timezone
# -----------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOCAL_TZ = ZoneInfo(TIMEZONE)

# -----------------------------
# Defaults
# -----------------------------
SOC_SKIP_THRESHOLD = SOC_SKIP_THRESHOLD or 80
SOLAR_POWER_SKIP_W = SOLAR_POWER_SKIP_W or 800
PEAK_START = PEAK_START or datetime.strptime("16:00", "%H:%M").time()
PEAK_END = PEAK_END or datetime.strptime("19:00", "%H:%M").time()
GRACE_RETRY_INTERVAL = GRACE_RETRY_INTERVAL or 300
MAX_AGILE_PRICE_PPK = MAX_AGILE_PRICE_PPK or 22  # p/kWh default limit

# -----------------------------
# Track currently running schedule
# -----------------------------
active_schedule_id = None

# -----------------------------
# Helpers
# -----------------------------
def format_sec_to_hm(seconds: float) -> str:
    seconds = round(seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m"

def in_peak_window(dt: datetime) -> bool:
    t = dt.time()
    return PEAK_START <= t < PEAK_END

def should_retry(schedule_id: int) -> bool:
    last_retry = get_last_retry(schedule_id)
    now = datetime.utcnow()
    if not last_retry or (now - last_retry).total_seconds() >= GRACE_RETRY_INTERVAL:
        update_last_retry(schedule_id)
        return True
    return False

def fetch_agile_price_for_slot(schedule_start: str, schedule_end: str):
    try:
        start_utc = datetime.fromisoformat(schedule_start).astimezone(timezone.utc)
        end_utc = datetime.fromisoformat(schedule_end).astimezone(timezone.utc)

        period_from = (start_utc - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        period_to = (end_utc + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        url = f"{AGILE_URL}?period_from={period_from}&period_to={period_to}"

        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if "results" not in data:
            logging.warning("No results from Agile API.")
            return None

        for item in data["results"]:
            valid_from = datetime.fromisoformat(item["valid_from"].replace("Z", "+00:00"))
            valid_to = datetime.fromisoformat(item["valid_to"].replace("Z", "+00:00"))
            logging.warning(f"Price match found for timestamp between {valid_from} & {valid_to}.")
            if valid_from <= start_utc < valid_to:
                return float(item["value_inc_vat"])  # £→p

        logging.warning(f"No price match found for {schedule_start}.")
        return None

    except Exception as e:
        logging.error(f"Error fetching Agile price for slot: {e}")
        return None

# -----------------------------
# Safe shutdown
# -----------------------------
def safe_shutdown(signal_received=None, frame=None):
    global active_schedule_id

    if not active_schedule_id:
        logging.info("Executor interrupted — no active schedule, exiting cleanly.")
        sys.exit(0)

    logging.warning("⚠️ Executor interrupted — performing safe shutdown for active schedule...")
    try:
        status = get_battery_status()
        soc = status.get('percentage_charged') if status else None

        ok = set_charge(reserve=BATTERY_RESERVE_END, grid_charging=False)
        if ok:
            logging.info(f"✅ Safe shutdown: grid charging stopped. reserve={BATTERY_RESERVE_END}, SOC={soc}")
        else:
            logging.warning("⚠️ Safe shutdown attempted but API did not confirm stop.")

        add_decision(active_schedule_id, None, None, 'stopped', 'manual_interrupt', soc, None, None)
        mark_as_executed(active_schedule_id)
        logging.info(f"🛑 Schedule {active_schedule_id} marked as manually stopped.")

    except Exception as e:
        logging.error(f"Error during safe shutdown: {e}")
    finally:
        logging.info("👋 Executor exited cleanly.")
        sys.exit(0)

# -----------------------------
# Core Schedule Logic
# -----------------------------
def process_schedule_row(row, now: datetime):
    global active_schedule_id
    #schedule_id, start_iso, end_iso, mode = row[:4]
    schedule_id = row["id"]
    start_iso = row["start_time"]
    end_iso = row["end_time"]
    mode = row["mode"]
    executed = row["executed"]
    created_at = row["created_at"]
    last_retry_utc = row["last_retry_utc"]
    retry_count = row["retry_count"]
    expired = row["expired"]
    decision = row["decision"]
    decision_at = row["decision_at"]
    price_p_per_kwh = row["price_p_per_kwh"]

    logging.info(f"Processing schedule {schedule_id}: {start_iso} → {end_iso}")

    try:
        start_dt = datetime.fromisoformat(start_iso).replace(tzinfo=LOCAL_TZ)
        end_dt = datetime.fromisoformat(end_iso).replace(tzinfo=LOCAL_TZ)
    except Exception:
        logging.error("Invalid datetime format; marking executed.")
        mark_as_executed(schedule_id, "Errored - Invalid datatime")
        add_decision(schedule_id, start_iso, end_iso, 'error', 'bad_datetime', None, None, None)
        return

    status = get_battery_status()
    if not status:
        logging.warning("Could not read battery status; skipping.")
        return

    soc = status.get('percentage_charged', 0.0)
    island = status.get('island_status', 'unknown') or 'unknown'
    solar_power = status.get('solar_power', 0)

    if island.lower().startswith('off_grid'):
        if should_retry(schedule_id):
            logging.warning(f"Schedule {schedule_id} delayed — off-grid.")
        return

    if in_peak_window(start_dt) or in_peak_window(end_dt):
        add_decision(schedule_id, start_iso, end_iso, 'cancelled', 'peak_window', soc, solar_power, island,price_p_per_kwh)
        mark_as_executed(schedule_id, "Cancelled - Peak window")
        return

    if soc >= SOC_SKIP_THRESHOLD:
        add_decision(schedule_id, start_iso, end_iso, 'cancelled', f"soc_high_{soc}", soc, solar_power, island,price_p_per_kwh)
        mark_as_executed(schedule_id, "Cancelled - High SOC")
        return

   # 1️⃣ Expired
    if now > end_dt:
        logging.warning(f"⏰ Schedule {schedule_id} has expired (End: {end_dt}, Now: {now})")
        mark_as_executed(schedule_id, "expired")
        return

    # 2️⃣ Upcoming
    if now < start_dt:
        delta = (start_dt - now).total_seconds()
        logging.info(f"🕒 Waiting for schedule {schedule_id} (starts in {delta/60:.1f} min)")
        time.sleep(min(delta, 60))  # check every minute
        return

    # 3️⃣ Active
    if start_dt <= now < end_dt:
        active_schedule_id = schedule_id
        stored_price = get_stored_price(schedule_id)
        current_price = fetch_agile_price_for_slot(start_iso, end_iso)
        mark_as_executed(schedule_id, "started")

        if current_price is None:
            logging.warning(f"⚠️ Agile price unavailable, fallback to stored {stored_price}p/kWh")
            current_price = stored_price

        logging.info(f"💰 Current Agile price: {current_price}p/kWh | Stored: {stored_price}p/kWh")

        if current_price and current_price > MAX_AGILE_PRICE_PPK:
            add_decision(schedule_id, start_iso, end_iso, 'cancelled',
                         f"price_high_{current_price}p>limit_{MAX_AGILE_PRICE_PPK}p",
                         soc, solar_power, island,price_p_per_kwh)
            mark_as_executed(schedule_id, f"Cancelled - Price too high ({current_price}p/kWh)")
            logging.warning(f"Skipping charge — price too high ({current_price}p/kWh).")
            return

        try:
            set_charge(reserve=BATTERY_RESERVE_START, grid_charging=True)
            logging.info(f"⚡ Charging started for schedule {schedule_id}, reserve={BATTERY_RESERVE_START}")
            duration = (end_dt - now).total_seconds()
            logging.info(f"Sleeping {duration/60:.1f} min until end of schedule {schedule_id}")
            time.sleep(duration)

            set_charge(reserve=BATTERY_RESERVE_END, grid_charging=False)
            mark_as_executed(schedule_id, "completed")
            add_decision(schedule_id, start_iso, end_iso, "completed", "Successful",
                         soc, solar_power, island,price_p_per_kwh)
            logging.info(f"⚡ Charging ended for schedule {schedule_id}, reserve={BATTERY_RESERVE_END}")
            duration = (end_dt - now).total_seconds()
            logging.info(f"Sleeping {duration/60:.1f} min until end of schedule {schedule_id}")
            time.sleep(duration)
        except KeyboardInterrupt:
                safe_shutdown()
        except Exception as e:
            logging.error(f"❌ Error during schedule {schedule_id}: {e}")
            set_charge(reserve=BATTERY_RESERVE_END, grid_charging=False)
            mark_as_executed(schedule_id, "aborted")
            add_decision(schedule_id, start_iso, end_iso, 'aborted', 'System_Error',
                     soc, solar_power, island,price_p_per_kwh)
        finally:
            active_schedule_id = None
   
"""
    if now >= end_dt:
        ok = set_charge(reserve=BATTERY_RESERVE_END, grid_charging=False)
        action = 'Stopped' if ok else 'stop_failed'
        if action == 'Stopped':
           add_decision(schedule_id, start_iso, end_iso, action, 'Successful', soc, solar_power, island,price_p_per_kwh)
        else:
           add_decision(schedule_id, start_iso, end_iso, action, 'API_Error', soc, solar_power, island,price_p_per_kwh)
        mark_as_executed(schedule_id)
        if active_schedule_id == schedule_id:
            active_schedule_id = None
        logging.info(f"⏹️ Schedule {schedule_id} ended — {action}. reserve={BATTERY_RESERVE_END}")
        return
"""
# -----------------------------
# Main Loop
# -----------------------------
def main():
    signal.signal(signal.SIGINT, safe_shutdown)
    signal.signal(signal.SIGTERM, safe_shutdown)

    logging.info("Executor started — polling DB for pending schedules.")
    try:
        while True:
            now = datetime.now(LOCAL_TZ)

            mark_all_expired(now)
            rows = fetch_pending_schedules()

            active_rows = []
            for row in rows:
                schedule_id, start_iso, end_iso, mode = row[:4]
                end_dt = datetime.fromisoformat(end_iso).replace(tzinfo=LOCAL_TZ)

                active_rows.append(row)

            if not active_rows:
                logging.debug("No active schedules remaining, sleeping idle.")
                time.sleep(EXECUTOR_IDLE_SLEEP_SEC)
                continue

            future_starts = [
                datetime.fromisoformat(r[1]).replace(tzinfo=LOCAL_TZ)
                for r in active_rows
                if datetime.fromisoformat(r[1]).replace(tzinfo=LOCAL_TZ) > now
            ]

            triggerable_rows = [
                r for r in active_rows
                if datetime.fromisoformat(r[1]).replace(tzinfo=LOCAL_TZ) <= now + timedelta(seconds=EXECUTOR_SLEEP_AHEAD_SEC)
            ]

            for row in triggerable_rows:
                try:
                    process_schedule_row(row, now)
                except Exception as e:
                    logging.exception(f"Unexpected error processing schedule {row[0]}: {e}")

            if future_starts:
                next_start = min(future_starts)
                sleep_seconds = (next_start - now).total_seconds() - EXECUTOR_SLEEP_AHEAD_SEC
                sleep_seconds = max(sleep_seconds, EXECUTOR_POLL_INTERVAL)
                logging.info(f"⚙️ Executor active — maintaining current schedule state.")
                logging.info(f"Sleeping {format_sec_to_hm(sleep_seconds)} until next schedule.")
                time.sleep(sleep_seconds)
            else:
                logging.debug(f"No future schedules, sleeping idle for {format_sec_to_hm(EXECUTOR_IDLE_SLEEP_SEC)}.")
                time.sleep(EXECUTOR_IDLE_SLEEP_SEC)

    except KeyboardInterrupt:
        safe_shutdown()


if __name__ == "__main__":
    main()
