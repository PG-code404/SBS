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
    AGILE_URL, MAX_AGILE_PRICE_PPK,SCHEDULER_RUNS_PER_DAY
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
            if valid_from <= start_utc < valid_to:
                return float(item["value_inc_vat"])  # £→p
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

        set_charge(reserve=BATTERY_RESERVE_END, grid_charging=False)
        logging.info(f"✅ Safe shutdown: grid charging stopped. reserve={BATTERY_RESERVE_END}, SOC={soc}")

        add_decision(active_schedule_id, None, None, 'stopped', 'manual_interrupt', soc, None, None)
        mark_as_executed(active_schedule_id)
        logging.info(f"🛑 Schedule {active_schedule_id} marked as manually stopped.")

    except Exception as e:
        logging.error(f"Error during safe shutdown: {e}")
    finally:
        sys.exit(0)

# -----------------------------
# Core Schedule Logic
# -----------------------------
def process_schedule_row(row, now: datetime):
    global active_schedule_id
    schedule_id = row["id"]
    start_iso = row["start_time"]
    end_iso = row["end_time"]

    logging.info(f"Processing schedule {schedule_id}: {start_iso} → {end_iso}")

    try:
        start_dt = datetime.fromisoformat(start_iso).replace(tzinfo=LOCAL_TZ)
        end_dt = datetime.fromisoformat(end_iso).replace(tzinfo=LOCAL_TZ)
    except Exception:
        logging.error("Invalid datetime format; marking executed.")
        mark_as_executed(schedule_id, "Errored - Invalid datetime")
        add_decision(schedule_id, start_iso, end_iso, 'error', 'bad_datetime', None, None, None)
        return

    status = get_battery_status()
    if not status:
        logging.warning("Could not read battery status; skipping.")
        return

    soc = status.get('percentage_charged', 0.0)
    island = status.get('island_status', 'unknown') or 'unknown'
    solar_power = status.get('solar_power', 0)

    # Skip if off-grid
    if island.lower().startswith('off_grid') and not should_retry(schedule_id):
        logging.warning(f"Schedule {schedule_id} delayed — off-grid.")
        return

    # Cancel if peak
    if in_peak_window(start_dt) or in_peak_window(end_dt):
        add_decision(schedule_id, start_iso, end_iso, 'cancelled', 'peak_window', soc, solar_power, island)
        mark_as_executed(schedule_id, "cancelled")
        return

    # Cancel if SOC too high
    if soc >= SOC_SKIP_THRESHOLD:
        add_decision(schedule_id, start_iso, end_iso, 'cancelled', f"soc_high_{soc}", soc, solar_power, island)
        mark_as_executed(schedule_id, "cancelled")
        return

    # Expired
    if now > end_dt:
        logging.warning(f"⏰ Schedule {schedule_id} has expired (End: {end_dt}, Now: {now})")
        mark_as_executed(schedule_id, "expired")
        return

    # Upcoming — sleep until start
    if now < start_dt:
        delta = (start_dt - now).total_seconds()
        logging.info(f"🕒 Waiting for schedule {schedule_id} (starts in {delta/60:.1f} min)")
        time.sleep(min(delta, 60))  # heartbeat every 1 min
        return

    # Active
    if start_dt <= now < end_dt:
        active_schedule_id = schedule_id
        stored_price = get_stored_price(schedule_id)
        current_price = fetch_agile_price_for_slot(start_iso, end_iso) or stored_price

        logging.info(f"💰 Current Agile price: {current_price}p/kWh | Stored: {stored_price}p/kWh")

        if current_price > MAX_AGILE_PRICE_PPK:
            add_decision(schedule_id, start_iso, end_iso, 'cancelled',
                         f"price_high_{current_price}p>limit_{MAX_AGILE_PRICE_PPK}p",
                         soc, solar_power, island)
            mark_as_executed(schedule_id, "cancelled")
            logging.warning(f"Skipping charge — price too high ({current_price}p/kWh).")
            active_schedule_id = None
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
                         soc, solar_power, island)
            logging.info(f"⚡ Charging ended for schedule {schedule_id}, reserve={BATTERY_RESERVE_END}")
        except KeyboardInterrupt:
            safe_shutdown()
        except Exception as e:
            logging.error(f"❌ Error during schedule {schedule_id}: {e}")
            set_charge(reserve=BATTERY_RESERVE_END, grid_charging=False)
            mark_as_executed(schedule_id, "aborted")
            add_decision(schedule_id, start_iso, end_iso, 'aborted', 'System_Error',
                         soc, solar_power, island)
        finally:
            active_schedule_id = None

# -----------------------------
# Scheduler trigger
# -----------------------------
def maybe_run_scheduler(last_run_time, runs_per_day=1):
    from ScheduleChargeSlots import generate_schedules
    now = datetime.now(LOCAL_TZ).replace(tzinfo=None)
    interval_hours = 24 / runs_per_day
    if (not last_run_time) or ((now - last_run_time).total_seconds() >= interval_hours * 3600):
        logging.info(f"🗓️ Running scheduler (every {interval_hours:.1f} hours)...")
        try:
            generate_schedules()
            last_run_time = now
            logging.info("✅ Scheduler completed successfully.")
        except Exception as e:
            logging.error(f"❌ Scheduler failed: {e}")
    return last_run_time

def sleep_with_heartbeat(total_seconds):
    """Sleep in small intervals and log heartbeat to show executor is alive."""
    slept = 0
    while slept < total_seconds:
        sleep_chunk = min(HEARTBEAT_INTERVAL, total_seconds - slept)
        time.sleep(sleep_chunk)
        slept += sleep_chunk
        logging.debug(f"💓 Executor heartbeat — {format_sec_to_hm(total_seconds - slept)} until next schedule")

HEARTBEAT_INTERVAL = 60  # seconds
# -----------------------------
# Main Loop
# -----------------------------
def main():
    signal.signal(signal.SIGINT, safe_shutdown)
    signal.signal(signal.SIGTERM, safe_shutdown)

    logging.info("Executor started — polling DB for pending schedules.")
    last_scheduler_run = None
    runs_per_day = max(1, SCHEDULER_RUNS_PER_DAY)

    while True:
        now = datetime.now(LOCAL_TZ)
        mark_all_expired(now)
        last_scheduler_run = maybe_run_scheduler(last_scheduler_run, runs_per_day)
        rows = fetch_pending_schedules()

        if not rows:
            logging.debug("No pending schedules, sleeping idle.")
            sleep_with_heartbeat(EXECUTOR_IDLE_SLEEP_SEC)
            continue

        # Only pick the first schedule that is active or about to start within sleep ahead
        next_row = None
        next_start = None
        for row in rows:
            start_dt = datetime.fromisoformat(row["start_time"]).replace(tzinfo=LOCAL_TZ)
            end_dt = datetime.fromisoformat(row["end_time"]).replace(tzinfo=LOCAL_TZ)

            if start_dt <= now < end_dt:
                next_row = row
                break
            elif 0 <= (start_dt - now).total_seconds() <= EXECUTOR_SLEEP_AHEAD_SEC:
                next_row = row
                break
            elif not next_start or start_dt < next_start:
                next_start = start_dt

        if next_row:
            process_schedule_row(next_row, now)
        else:
            # Sleep until next schedule or idle
            if next_start:
                sleep_seconds = (next_start - now).total_seconds() - EXECUTOR_SLEEP_AHEAD_SEC
                sleep_seconds = max(sleep_seconds, EXECUTOR_POLL_INTERVAL)
                logging.info(f"⚙️ Executor active — awaiting next schedule in {format_sec_to_hm(sleep_seconds)}")
            else:
                sleep_seconds = EXECUTOR_IDLE_SLEEP_SEC
                logging.info(f"No upcoming schedules, sleeping idle for {format_sec_to_hm(sleep_seconds)}")
            sleep_with_heartbeat(sleep_seconds)

if __name__ == "__main__":
    from Keep_Alive import keep_alive
    keep_alive()
    main()
