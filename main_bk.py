import os
from dotenv import load_dotenv
import logging
import time
import requests
import sys
import signal
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from src.events import executor_wake_event

# To calculate CPU time:
class CPUMeter:
    def __init__(self):
        self.start_cpu = time.process_time()

    def cpu_used(self):
        return time.process_time() - self.start_cpu

cpu_meter = CPUMeter()

load_dotenv()
#from config_loader import config, DB_PATH, FLASK_SECRET_KEY

from config.config import (
    DB_PATH, DB_NAMESPACE, TIMEZONE, BATTERY_RESERVE_START,
    BATTERY_RESERVE_END, SOC_SKIP_THRESHOLD, PEAK_START, PEAK_END,
    SOLAR_POWER_SKIP_W, SIMULATION_MODE, EXECUTOR_POLL_INTERVAL,
    EXECUTOR_SLEEP_AHEAD_SEC, EXECUTOR_IDLE_SLEEP_SEC, GRACE_RETRY_INTERVAL,
    AGILE_URL, MAX_AGILE_PRICE_PPK, SCHEDULER_RUNS_PER_DAY, KEEP_ALIVE_API_KEY,
    CHARGE_RATE_KW, CLOUD_RUN_URL)
from src.db import (init_db, fetch_pending_schedules, mark_as_executed,
                    add_decision, get_last_retry, update_last_retry,
                    get_stored_price, mark_all_expired,get_next_schedule)
from src.netzero_api import get_battery_status, set_charge

from src.SolarData import hasEnoughSolar, fetch_solar_data

from src.Octopus_saving_sessions import get_kraken_token, get_saving_sessions, is_in_saving_session

PROCESS_START_TIME = datetime.now()

# -----------------------------
# Logging & Timezone
# -----------------------------
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
LOCAL_TZ = ZoneInfo(TIMEZONE)

# -----------------------------
# Defaults
# -----------------------------
SOC_SKIP_THRESHOLD = SOC_SKIP_THRESHOLD or 80
SOLAR_POWER_SKIP_W = SOLAR_POWER_SKIP_W or 800
PEAK_START = PEAK_START or datetime.strptime("16:00", "%H:%M").time()
PEAK_END = PEAK_END or datetime.strptime("19:00", "%H:%M").time()
GRACE_RETRY_INTERVAL = GRACE_RETRY_INTERVAL or 300
MAX_AGILE_PRICE_PPK = MAX_AGILE_PRICE_PPK or 15  # p/kWh default limit
HEARTBEAT_INTERVAL = 60  # seconds

# -----------------------------
# Executor Status
# -----------------------------
EXECUTOR_STATUS = {
    "active_schedule_id": None,
    "current_price": None,
    "soc": None,
    "solar_power": None,
    "island": None,
    "message": "Executor initialized",
    "next_schedule_time": None,
    "last_scheduler_run": None,
}

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
    now = datetime.now(timezone.utc)
    if not last_retry or (now -
                          last_retry).total_seconds() >= GRACE_RETRY_INTERVAL:
        update_last_retry(schedule_id)
        return True
    return False


def fetch_agile_price_for_slot(schedule_start: str, schedule_end: str):
    try:
        start_utc = datetime.fromisoformat(schedule_start).astimezone(
            timezone.utc)
        end_utc = datetime.fromisoformat(schedule_end).astimezone(timezone.utc)

        period_from = (start_utc - timedelta(hours=1)).isoformat().replace(
            "+00:00", "Z")
        period_to = (end_utc + timedelta(hours=1)).isoformat().replace(
            "+00:00", "Z")
        url = f"{AGILE_URL}?period_from={period_from}&period_to={period_to}"

        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if "results" not in data:
            logging.warning("No results from Agile API.")
            return None

        for item in data["results"]:
            valid_from = datetime.fromisoformat(item["valid_from"].replace(
                "Z", "+00:00"))
            valid_to = datetime.fromisoformat(item["valid_to"].replace(
                "Z", "+00:00"))
            if valid_from <= start_utc < valid_to:
                return float(item["value_inc_vat"])  # £→p
        return None
    except Exception as e:
        logging.error(f"Error fetching Agile price for slot: {e}")
        return None


def post_status_to_dashboard():
    """Push current executor status to Keep_Alive dashboard."""
    try:
        port = os.getenv("KEEP_ALIVE_PORT", "8080")
        urls = [
            f"http://localhost:{port}/update_status",
            f"http://127.0.0.1:{port}/update_status",
            f"{CLOUD_RUN_URL}/update_status"
        ]
        headers = {
            "x-api-key": KEEP_ALIVE_API_KEY,
            "Content-Type": "application/json"
        } if KEEP_ALIVE_API_KEY else {}

        payload = {
            "message":
            EXECUTOR_STATUS.get("message"),
            "last_scheduler_run":
            EXECUTOR_STATUS.get("last_scheduler_run"),
            "next_schedule_time":
            EXECUTOR_STATUS.get("next_schedule_time")
            if EXECUTOR_STATUS.get("next_schedule_time") else None,
            "active_schedule_id":
            EXECUTOR_STATUS.get("active_schedule_id"),
            "current_price":
            EXECUTOR_STATUS.get("current_price"),
            "soc":
            EXECUTOR_STATUS.get("soc"),
            "solar_power":
            EXECUTOR_STATUS.get("solar_power"),
            "island":
            EXECUTOR_STATUS.get("island"),
        }

        for url in urls:
            try:
                r = requests.post(url,
                                  json=payload,
                                  headers=headers,
                                  timeout=3)
                logging.debug(
                    f"POST to {url} returned {r.status_code} / {r.text}")
            except Exception as e:
                logging.debug(f"Could not post to {url}: {e}")
                #logging.error(f"POST to {url} failed: {e}")

    except Exception as e:
        logging.info(f"Could not post status to dashboard: {e}")


def sleep_with_heartbeat(total_seconds):
    slept = 0
    while slept < total_seconds:
        sleep_chunk = min(HEARTBEAT_INTERVAL, total_seconds - slept)

        # Wait with interrupt
        if executor_wake_event.wait(timeout=sleep_chunk):
            # Event triggered – clear it and break early
            executor_wake_event.clear()
            logging.info("[Executor] Woken early due to new schedule or manual trigger.")
            break

        slept += sleep_chunk
        remaining = total_seconds - slept

        EXECUTOR_STATUS.update({
            "message": f"Idle — sleeping {format_sec_to_hm(remaining)} until next schedule",
            "next_schedule_time":f"{format_sec_to_hm(remaining)}",
            "active_schedule_id": None
        })
        post_status_to_dashboard()
        logging.debug(EXECUTOR_STATUS["message"])




# -----------------------------
# Safe shutdown
# -----------------------------
def safe_shutdown(signal_received=None, frame=None):
    global active_schedule_id
    if not active_schedule_id:
        logging.info(
            "Executor interrupted — no active schedule, exiting cleanly.")
        EXECUTOR_STATUS.update({
            "active_schedule_id":None
        })
        post_status_to_dashboard()
        sys.exit(0)

    logging.warning(
        "⚠️ Executor interrupted — performing safe shutdown for active schedule..."
    )
    try:
        status = get_battery_status()
        soc = status.get('percentage_charged') if status else None

        set_charge(reserve=BATTERY_RESERVE_END, grid_charging=False)
        logging.info(
            f"✅ Safe shutdown: grid charging stopped. reserve={BATTERY_RESERVE_END}, SOC={soc}"
        )

        add_decision(active_schedule_id, None, None, 'stopped',
                     'manual_interrupt', soc, None, None)
        mark_as_executed(active_schedule_id)
        logging.info(
            f"🛑 Schedule {active_schedule_id} marked as manually stopped.")

        EXECUTOR_STATUS.update({
            "active_schedule_id":
            None,
            "message":
            f"Manually stopped schedule {active_schedule_id}"
        })
        post_status_to_dashboard()

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
    OCTO_TOKEN = None
    SAVING_SESSIONS = []
    current_price = None  # defined early to avoid UnboundLocalError

    try:
        OCTO_TOKEN = get_kraken_token()
        SAVING_SESSIONS = get_saving_sessions(OCTO_TOKEN)
        logging.info(f"⚡ Saving Sessions loaded: {len(SAVING_SESSIONS)}")
    except Exception as e:
        logging.error(
            f"⚠️ Octopus Saving Sessions disabled — proceeding without: {e}")
        OCTO_TOKEN = None
        SAVING_SESSIONS = []

    logging.info(f"Processing schedule {schedule_id}: {start_iso} → {end_iso}")

    # Parse datetime safely
    try:
        start_dt = datetime.fromisoformat(start_iso).replace(tzinfo=LOCAL_TZ)
        end_dt = datetime.fromisoformat(end_iso).replace(tzinfo=LOCAL_TZ)
    except Exception:
        logging.error("Invalid datetime format; marking executed.")
        mark_as_executed(schedule_id, "cancelled")
        add_decision(schedule_id, start_iso, end_iso, 'error', 'bad_datetime',
                     None, None, None)
        return

    # Get battery status
    status = get_battery_status()
    if not status:
        logging.warning("Could not read battery status; skipping.")
        EXECUTOR_STATUS.update({
            "message": f"Schedule {schedule_id} skipped — battery status unavailable",
            "active_schedule_id": None
        })
        active_schedule_id = None
        post_status_to_dashboard()
        return

    soc = status.get('percentage_charged', 0.0)
    island = status.get('island_status', 'unknown') or 'unknown'
    solar_power = status.get('solar_power', 0)
    usr_grid_charging_enabled_settings = status.get('grid_charging', False) # Not used yet

    # Skip if off-grid and no retries left
    if island.lower().startswith('off_grid'):
        logging.info(f"Schedule {schedule_id} cancelled — off-grid.")
        EXECUTOR_STATUS.update({
            "message": f"Schedule {schedule_id} cancelled — off-grid",
            "active_schedule_id": None,
            "soc": soc,
            "solar_power": solar_power,
            "island": island,
            "current_price": current_price
        })
        post_status_to_dashboard()
        add_decision(schedule_id, start_iso, end_iso, 'cancelled',
                     'Powerwall off-grid', soc, solar_power, island)
        mark_as_executed(schedule_id, "cancelled")
        return
    
    """
    # More tests needed. Parked this for now. 
    # Skip if User has disabled grid charging and no retries left
    if not usr_grid_charging_enabled_settings:
        logging.info(f"Schedule {schedule_id} cancelled — grid charging disabled.")

        EXECUTOR_STATUS.update({
            "message": f"Schedule {schedule_id} cancelled — grid charging disabled",
            "active_schedule_id": None,
            "soc": soc,
            "solar_power": solar_power,
            "island": island,
            "current_price": current_price,
        })
        post_status_to_dashboard()
        add_decision(schedule_id, start_iso, end_iso, 'cancelled',
                     'Grid Charging disabled', soc, solar_power, island)
        mark_as_executed(schedule_id, "cancelled")
        active_schedule_id = None
        return
    """
    # --- Octopus Saving Session check ---
    # Skip charging as User is participating in Saving sessions
    try:
        if OCTO_TOKEN and SAVING_SESSIONS:
            if is_in_saving_session(start_dt, end_dt, SAVING_SESSIONS):
                logging.info(
                    f"❌ Schedule {schedule_id} cancelled — Octopus Saving Session"
                )
                mark_as_executed(schedule_id, "cancelled")
                add_decision(schedule_id, start_iso, end_iso, 'cancelled',
                             'Saving sessions', None, None, None)
                return
    except Exception as e:
        # Fail gracefully, simply ignore Octopus logic
        logging.error(
            f"⚠️ Saving Session check failed — continuing schedule: {e}")

    # --- Manual override logic ---
    manual_override = row["manual_override"]
    target_soc = row["target_soc"]

    if manual_override:
        # If grid charging disabled: either retry later or cancel if no retries left
        """if not usr_grid_charging_enabled_settings:
            if not should_retry(schedule_id):
                logging.info(f"Schedule {schedule_id} cancelled — grid charging disabled (manual override, no retries).")
                EXECUTOR_STATUS.update({
                    "message": f"Manual Schedule {schedule_id} cancelled — grid charging disabled",
                    "active_schedule_id": None,
                    "soc": soc,
                    "solar_power": solar_power,
                    "island": island,
                })
                post_status_to_dashboard()
                add_decision(schedule_id, start_iso, end_iso, 'cancelled',
                             'Grid Charging disabled (manual override)', soc, solar_power, island)
                mark_as_executed(schedule_id, "cancelled")
                return
            else:
                # will retry later; don't mark executed
                logging.info(f"Manual override for schedule {schedule_id} deferred — grid charging disabled (will retry).")
                EXECUTOR_STATUS.update({
                    "message": f"Manual override deferred — grid charging disabled",
                    "active_schedule_id": None,
                    "soc": soc,
                    "solar_power": solar_power,
                    "island": island,
                })
                post_status_to_dashboard()
                return
    """
        # Grid charging enabled -> perform manual override
        EXECUTOR_STATUS.update({
            "active_schedule_id": schedule_id,
            "message": f"Manual override active — charging to {target_soc}%"
        })
        post_status_to_dashboard()

        try:
            set_charge(reserve=target_soc, grid_charging=True)
            active_schedule_id = schedule_id
            logging.info(
                f"⚡ Manual override — charging schedule {schedule_id} to {target_soc}%"
            )

            # Recompute duration with fresh time
            now_local = datetime.now(LOCAL_TZ)
            duration = (end_dt - now_local).total_seconds()
            if duration <= 0:
                logging.warning(f"Manual override {schedule_id} expired before execution.")
                mark_as_executed(schedule_id, "expired")
                EXECUTOR_STATUS.update({"message": f"Manual override {schedule_id} expired", "active_schedule_id": None})
                post_status_to_dashboard()
                return

            elapsed = 0
            heartbeat_interval = 60  # seconds
            while elapsed < duration:
                sleep_chunk = min(heartbeat_interval, duration - elapsed)
                time.sleep(sleep_chunk)
                elapsed += sleep_chunk

                status = get_battery_status()
                soc = status.get('percentage_charged', soc) if status else soc
                EXECUTOR_STATUS.update({
                    "soc": soc,
                    "message": f"Manual override — charging to {target_soc}%, current SOC: {soc}%",
                    "active_schedule_id": schedule_id
                })
                post_status_to_dashboard()
                if soc >= target_soc:
                    logging.info(f"Target SOC {target_soc}% reached for schedule {schedule_id}")
                    break

            # Stop charging
            set_charge(reserve=BATTERY_RESERVE_END, grid_charging=False)
            mark_as_executed(schedule_id, "completed")
            add_decision(schedule_id, start_iso, end_iso, "completed",
                         "Manual override successful", soc, solar_power,
                         island)
            EXECUTOR_STATUS.update({
                "message": f"Manual override schedule {schedule_id} completed successfully",
                "active_schedule_id": None
            })
            active_schedule_id = None
            post_status_to_dashboard()
            logging.info(f"✅ Manual override schedule {schedule_id} ended, SOC={soc}%")

        except KeyboardInterrupt:
            safe_shutdown()
        except Exception as e:
            logging.error(f"❌ Error during manual override schedule {schedule_id}: {e}")
            set_charge(reserve=BATTERY_RESERVE_END, grid_charging=False)
            mark_as_executed(schedule_id, "aborted")
            add_decision(schedule_id, start_iso, end_iso, "aborted",
                         "Manual scheduling System_Error", soc, solar_power,
                         island)
            EXECUTOR_STATUS.update({"message": f"Manual override {schedule_id} aborted", "active_schedule_id": None})
            post_status_to_dashboard()
        finally:
            active_schedule_id = None
            EXECUTOR_STATUS.update({
                "active_schedule_id": None,
                "message": f"Manual override schedule {schedule_id} ended"
            })
            post_status_to_dashboard()
        return  # exit after manual override handling

    # --- Non-manual schedules ---
    # Cancel if scheduled in peak window
    if in_peak_window(start_dt) or in_peak_window(end_dt):
        add_decision(schedule_id, start_iso, end_iso, 'cancelled',
                     'peak_window', soc, solar_power, island)
        mark_as_executed(schedule_id, "cancelled")
        EXECUTOR_STATUS.update(
            {"message": f"Schedule {schedule_id} cancelled — peak window", "active_schedule_id": None})
        post_status_to_dashboard()
        return

    # Cancel if SOC too high
    if soc >= SOC_SKIP_THRESHOLD:
        add_decision(schedule_id, start_iso, end_iso, 'cancelled',
                     f"soc_high_{soc}", soc, solar_power, island)
        mark_as_executed(schedule_id, "cancelled")
        EXECUTOR_STATUS.update(
            {"message": f"Schedule {schedule_id} cancelled — SOC high {soc}%", "active_schedule_id": None})
        post_status_to_dashboard()
        return

    # Expired
    if now > end_dt:
        logging.warning(
            f"⏰ Schedule {schedule_id} has expired (End: {end_dt}, Now: {now})"
        )
        mark_as_executed(schedule_id, "expired")
        EXECUTOR_STATUS.update({"message": f"Schedule {schedule_id} expired", "active_schedule_id": None})
        active_schedule_id = None
        post_status_to_dashboard()
        return

    # Upcoming — wait until start
    if now < start_dt:
        delta = (start_dt - now).total_seconds()
        logging.info(
            f"🕒 Waiting for schedule {schedule_id} (starts in {delta/60:.1f} min)"
        )
        EXECUTOR_STATUS.update(
            {"message": f"Waiting to start schedule {schedule_id}", "active_schedule_id": None})
        post_status_to_dashboard()
        time.sleep(min(delta, 60))  # heartbeat every 1 min
        return

    # Active schedule (autonomous)
    active_schedule_id = schedule_id
    #EXECUTOR_STATUS["active_schedule_id"] = schedule_id
    stored_price = get_stored_price(schedule_id)
    current_price = fetch_agile_price_for_slot(start_iso,
                                               end_iso) or stored_price
    EXECUTOR_STATUS.update({
        "current_price": current_price,
        "soc": soc,
        "solar_power": solar_power,
        "island": island,
        "message": f"Charging from grid for schedule {schedule_id}",
        "active_schedule_id": active_schedule_id
    })
    post_status_to_dashboard()

    logging.info(
        f"💰 Current Agile price: {current_price}p/kWh | Stored: {stored_price}p/kWh"
    )

    if current_price is not None and current_price > MAX_AGILE_PRICE_PPK:
        add_decision(
            schedule_id, start_iso, end_iso, 'cancelled',
            f"price_high_{current_price}p>limit_{MAX_AGILE_PRICE_PPK}p", soc,
            solar_power, island)
        mark_as_executed(schedule_id, "cancelled")
        logging.warning(
            f"Skipping charge — price too high ({current_price}p/kWh).")
        EXECUTOR_STATUS.update(
            {"message": f"Schedule {schedule_id} cancelled — price too high", "active_schedule_id": None})
        post_status_to_dashboard()
        active_schedule_id = None
        return

    try:
        solar_only = hasEnoughSolar(start_dt,
                                    end_dt,
                                    target_energy_kwh=CHARGE_RATE_KW)
        if solar_only:
            set_charge(BATTERY_RESERVE_END, grid_charging=False)
            logging.info(
                "Solar sufficient for schedule — enabling solar-only charging."
            )
            add_decision(schedule_id, start_iso, end_iso, 'cancelled',
                         "Forecasted enough Solar for schedule", soc,
                         solar_power, island)
            EXECUTOR_STATUS.update({
                "message": f"Schedule {schedule_id} cancelled — Forecasted enough Solar", "active_schedule_id": None
            })
            mark_as_executed(schedule_id, "cancelled")
            post_status_to_dashboard()
            active_schedule_id = None
            return
        else: logging.info("Not enough Solar, charging should be from Grid")
    except Exception as e:
        logging.error(
            f"⚠️ Solar forecast check failed — proceeding with grid charging: {e}"
        )

    try:
        if soc < BATTERY_RESERVE_START:
            reserve_value = BATTERY_RESERVE_START
        else:
            reserve_value = SOC_SKIP_THRESHOLD

        set_charge(reserve=reserve_value, grid_charging=True)
        logging.info(
            f"⚡ Charging started for schedule {schedule_id}, reserve={reserve_value}"
        )
        # recompute duration against fresh time
        now_local = datetime.now(LOCAL_TZ)
        duration = (end_dt - now_local).total_seconds()
        if duration <= 0:
            logging.warning(f"Schedule {schedule_id} expired before action.")
            mark_as_executed(schedule_id, "expired")
            EXECUTOR_STATUS.update({"message": f"Schedule {schedule_id} expired", "active_schedule_id": None})
            post_status_to_dashboard()
            active_schedule_id = None
            return

        logging.info(
            f"Sleeping {duration/60:.1f} min until end of schedule {schedule_id}"
        )
        time.sleep(duration)
        mark_as_executed(schedule_id, "completed")
        add_decision(schedule_id, start_iso, end_iso, "completed",
                     "Successful", soc, solar_power, island)
        
        next_sched = get_next_schedule(end_dt, lookahead_minutes=30)

        if next_sched:
            next_id = next_sched["id"]
            next_start = datetime.fromisoformat(next_sched["start_time"]).replace(tzinfo=LOCAL_TZ)

            logging.info(f"⏭️ Next schedule {next_id} starts at {next_start} (<30 min) — ""keeping charging ON until next evaluation.")
            EXECUTOR_STATUS.update({"message": (f"Charging continues — next schedule {next_id} will be evaluated "f"at {next_start}"),
                                    "active_schedule_id": schedule_id})
            post_status_to_dashboard()
            # DON'T stop charging — return to let main loop evaluate next schedule
            return

        
        set_charge(reserve=BATTERY_RESERVE_END, grid_charging=False)
        logging.info(
            f"⚡ Charging ended for schedule {schedule_id}, reserve={BATTERY_RESERVE_END}"
        )
    except KeyboardInterrupt:
        safe_shutdown()
    except Exception as e:
        logging.error(f"❌ Error during schedule {schedule_id}: {e}")
        set_charge(reserve=BATTERY_RESERVE_END, grid_charging=False)
        mark_as_executed(schedule_id, "aborted")
        add_decision(schedule_id, start_iso, end_iso, 'aborted',
                     'System_Error', soc, solar_power, island)
    finally:
        
        EXECUTOR_STATUS.update({
            "active_schedule_id": None,
            "message": f"Schedule {schedule_id} ended"
        })
        active_schedule_id = None
        post_status_to_dashboard()


# -----------------------------
# Scheduler trigger
# -----------------------------
def maybe_run_scheduler(last_run_time, runs_per_day=1):
    from src.ScheduleChargeSlots import generate_schedules
    now = datetime.now(LOCAL_TZ).replace(tzinfo=None)
    interval_hours = 24 / runs_per_day
    if (not last_run_time) or ((now - last_run_time).total_seconds()
                               >= interval_hours * 3600):
        logging.info(
            f"🗓️ Running scheduler (every {interval_hours:.1f} hours)...")
        try:
            generate_schedules()
            last_run_time = now
            logging.info("✅ Scheduler completed successfully.")
        except Exception as e:
            logging.error(f"❌ Scheduler failed: {e}")
    return last_run_time


# -----------------------------
# Main Loop
# -----------------------------
def main():
    logging.debug(f"[Executor Init in main.py] event id={id(executor_wake_event)}")

    signal.signal(signal.SIGINT, safe_shutdown)
    signal.signal(signal.SIGTERM, safe_shutdown)

    logging.info("Executor started — Ready to query DB for pending schedules.")
    init_db()
    last_scheduler_run = None
    runs_per_day = max(1, SCHEDULER_RUNS_PER_DAY)
    prev_cpu = 0.0

    while True:
        
        current_cpu = cpu_meter.cpu_used()
        logging.info(f"[CPU] Interval CPU used: {current_cpu - prev_cpu:.4f} sec")
        prev_cpu = current_cpu
        
        now = datetime.now(LOCAL_TZ)
        # Marking schedules expired that are past end time before fetching pending schedules
        mark_all_expired(now)
        # Call to get forecasted solar data
        fetch_solar_data()
        # Begin to check if scheduler needs to run again
        last_scheduler_run = maybe_run_scheduler(last_scheduler_run,runs_per_day)
        last_scheduler_run_iso = last_scheduler_run.isoformat(
        ) if last_scheduler_run else None
        EXECUTOR_STATUS.update({"last_scheduler_run": last_scheduler_run_iso})
        post_status_to_dashboard()

        rows = fetch_pending_schedules()

        if not rows:
            logging.debug("No pending schedules, sleeping idle.")
            EXECUTOR_STATUS.update({"message": "No pending schedules — idle",
                                    "active_schedule_id": None})
            logging.debug(f"POSTING STATUS: {EXECUTOR_STATUS}")
            post_status_to_dashboard()
            sleep_with_heartbeat(EXECUTOR_IDLE_SLEEP_SEC)
            continue

        # Pick next active or upcoming schedule
        next_row = None
        next_start = None
        for row in rows:
            start_dt = datetime.fromisoformat(
                row["start_time"]).replace(tzinfo=LOCAL_TZ)
            end_dt = datetime.fromisoformat(
                row["end_time"]).replace(tzinfo=LOCAL_TZ)

            if start_dt <= now < end_dt:
                next_row = row
                break
            elif 0 <= (start_dt -
                       now).total_seconds() <= EXECUTOR_SLEEP_AHEAD_SEC:
                next_row = row
                break
            elif not next_start or start_dt < next_start:
                next_start = start_dt

        if next_row:
            process_schedule_row(next_row, now)

        else:
            # Sleep until next schedule or idle
            if next_start:
                sleep_seconds = (next_start - now
                                 ).total_seconds() - EXECUTOR_SLEEP_AHEAD_SEC
                sleep_seconds = max(sleep_seconds, EXECUTOR_POLL_INTERVAL)
                EXECUTOR_STATUS.update({
                    "message":
                    f"Awaiting next schedule in {format_sec_to_hm(sleep_seconds)}"
                })
                EXECUTOR_STATUS.update(
                    {"next_schedule_time": format_sec_to_hm(sleep_seconds)})
                logging.info(
                    f"⚙️ Executor active — awaiting next schedule in {format_sec_to_hm(sleep_seconds)}"
                )
                logging.debug(
                    f"POSTING STATUS: {EXECUTOR_STATUS} before POST call")
                post_status_to_dashboard()
                logging.debug(f"called EXECUTOR_STATUS POST in the else block")
            else:
                sleep_seconds = EXECUTOR_IDLE_SLEEP_SEC
                EXECUTOR_STATUS.update({
                    "message":
                    f"No upcoming schedules — idle for {format_sec_to_hm(sleep_seconds)}"
                })
                logging.debug(EXECUTOR_STATUS["message"])
                post_status_to_dashboard()
            post_status_to_dashboard()
            sleep_with_heartbeat(sleep_seconds)


if __name__ == "__main__":
    from src.Keep_Alive import keep_alive
    keep_alive()
    main()
