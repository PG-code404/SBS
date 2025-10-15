import requests
import logging
from config import NETZERO_API_KEY, SITE_ID, NETZERO_URL_TEMPLATE, SIMULATION_MODE

NETZERO_URL = NETZERO_URL_TEMPLATE.format(SITE_ID=SITE_ID)

# -----------------------------
# Set grid charging / reserve only (no operational_mode toggles)
# -----------------------------
def set_charge(reserve: int, grid_charging: bool) -> bool:
    """
    Only updates backup_reserve_percent and grid_charging.
    Returns True on success (or in simulation), False on failure.
    """
    if SIMULATION_MODE:
        logging.info(f"[SIMULATION] set_charge: reserve={reserve} grid_charging={grid_charging}")
        return True

    payload = {
        "backup_reserve_percent": reserve,
        "grid_charging": grid_charging
    }
    headers = {"Authorization": f"Bearer {NETZERO_API_KEY}", "Content-Type": "application/json"}

    try:
        resp = requests.post(NETZERO_URL, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        logging.info(f"NetZero set_charge ok: reserve={reserve} grid_charging={grid_charging}")
        return True
    except requests.RequestException as e:
        logging.error(f"NetZero set_charge failed: {e}")
        return False

# -----------------------------
# Get battery status (stable shape)
# -----------------------------
def get_battery_status():
    """
    Returns dict with keys (consistent):
      - percentage_charged (float)
      - live_percentage_charged (float, if present)
      - grid_charging (bool)
      - grid_status (str)
      - island_status (str)
      - battery_power (int/float)
      - solar_power (int/float)
      - load_power (int/float)
      - timestamp (str)
    Returns None on error.
    """
    if SIMULATION_MODE:
        # sensible simulated response for testing
        fake = {
            "percentage_charged": 58.5,
            "grid_charging": False,
            "grid_status": "Active",
            "island_status": "on_grid",
            "battery_power": 0,
            "solar_power": 1500,
            "load_power": 300,
            "timestamp": "2025-10-10T17:36:03+01:00"
        }
        logging.info(f"[SIMULATION] get_battery_status -> {fake}")
        return fake

    try:
        headers = {"Authorization": f"Bearer {NETZERO_API_KEY}"}
        resp = requests.get(NETZERO_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        live = data.get("live_status", {}) or {}
        # prefer live.percentage_charged if available
        percentage = None
        if live.get("percentage_charged") is not None:
            percentage = live.get("percentage_charged")
        else:
            percentage = data.get("percentage_charged")

        result = {
            "percentage_charged": round(float(percentage) if percentage is not None else 0.0, 2),
            "grid_charging": bool(data.get("grid_charging", False)),
            "grid_status": live.get("grid_status") or data.get("grid_status"),
            "island_status": live.get("island_status") or data.get("island_status") or "unknown",
            "battery_power": live.get("battery_power"),
            "solar_power": live.get("solar_power"),
            "load_power": live.get("load_power"),
            "timestamp": live.get("timestamp") or data.get("timestamp")
        }
        logging.info(f"NetZero status: SoC={result['percentage_charged']}%, island={result['island_status']}, grid_charging={result['grid_charging']}")
        return result

    except requests.RequestException as e:
        logging.error(f"NetZero GET failed: {e}")
        return None
