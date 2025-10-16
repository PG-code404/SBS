import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple
from config import DB_PATH, DB_NAMESPACE, DECISIONS_DB_TABLE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# -----------------------------
# DB Connection
# -----------------------------
def get_connection():
    """Return a SQLite connection with default row factory."""
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


# -----------------------------
# Schema helpers (safe migrations)
# -----------------------------
def _table_columns(table: str) -> List[str]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table});")
    rows = cur.fetchall()
    conn.close()
    return [r["name"] for r in rows]


def _ensure_columns():
    """
    Ensure expected columns exist on schedules table; add them if missing.
    Non-destructive: uses ALTER TABLE ADD COLUMN for missing fields.
    """
    conn = get_connection()
    cur = conn.cursor()

    # Base table
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {DB_NAMESPACE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            mode TEXT NOT NULL,
            executed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    existing = _table_columns(DB_NAMESPACE)

    # Add optional columns safely
    optional_columns = {
        "last_retry_utc": "TEXT DEFAULT NULL",
        "retry_count": "INTEGER DEFAULT 0",
        "expired": "INTEGER DEFAULT 0",
        "decision": "TEXT DEFAULT NULL",
        "decision_at": "TEXT DEFAULT NULL",
        "price_p_per_kwh": "REAL DEFAULT NULL"
    }

    for col, col_def in optional_columns.items():
        if col not in existing:
            logging.info(f"Adding missing column '{col}' to schedules table.")
            cur.execute(f"ALTER TABLE {DB_NAMESPACE} ADD COLUMN {col} {col_def};")

    conn.commit()

    # Unique index
    try:
        cur.execute(f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{DB_NAMESPACE}_start_end
            ON {DB_NAMESPACE} (start_time, end_time)
        """)
        conn.commit()
    except Exception as e:
        logging.warning(f"Could not create unique index idx_{DB_NAMESPACE}_start_end: {e}")

    conn.close()

    # Ensure decisions table
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {DECISIONS_DB_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            schedule_id INTEGER,
            start_time TEXT,
            end_time TEXT,
            action TEXT,
            reason TEXT,
            soc REAL,
            solar_power REAL,
            island_status TEXT,
            price_p_per_kwh REAL
        )
    """)
    conn.commit()
    conn.close()


# -----------------------------
# Initialize DB (idempotent)
# -----------------------------
def init_db():
    """Initialize DB and ensure schema is up-to-date."""
    _ensure_columns()
    logging.info("DB initialized and schema ensured.")


# -----------------------------
# Schedules helpers
# -----------------------------

import sqlite3
from datetime import datetime, timedelta
from config import DB_PATH

# ---------------------------------------
# Agile Tariff Helpers
# ---------------------------------------

# def get_current_agile_price():
#     """
#     Fetch the most recent Agile price (in p/kWh) and 24h average.
#     Returns (current_price, average_price) — both floats.
#     """
#     try:
#         conn = sqlite3.connect(DB_PATH)
#         cur = conn.cursor()
#
#         # Latest price from Agile table (must have columns: timestamp, price_p_per_kwh)
#         cur.execute("""
#             SELECT price_p_per_kwh, timestamp
#             FROM agile_prices
#             ORDER BY timestamp DESC
#             LIMIT 1
#         """)
#         latest = cur.fetchone()
#
#         if not latest:
#             return None, None
#
#         current_price = float(latest[0])
#
#         # Calculate last 24h average
#         since = datetime.utcnow() - timedelta(hours=24)
#         cur.execute("""
#             SELECT AVG(price_p_per_kwh)
#             FROM agile_prices
#             WHERE timestamp >= ?
#         """, (since.isoformat(),))
#         avg_price = cur.fetchone()[0] or current_price
#
#         return current_price, float(avg_price)
#
#     except Exception as e:
#         print(f"[DB] Error fetching Agile price: {e}")
#         return None, None
#     finally:
#         conn.close()

def get_stored_price(schedule_id):
    """
    Return the stored price (p/kWh) for the given schedule_id.
    Fallbacks to the last known Agile price or a safe default if unavailable.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # Try to get price stored specifically for this schedule
        cur.execute(f"""
            SELECT price_p_per_kwh
            FROM {DB_NAMESPACE}
            WHERE id = ?
        """, (schedule_id,))
        row = cur.fetchone()

        if row and row[0] is not None:
            return float(row[0])

        row = cur.fetchone()
        return float(row[0]) if row else 20.0  # default fallback

    except Exception as e:
        print(f"[DB] Error reading stored Agile price for schedule {schedule_id}: {e}")
        return 20.0
    finally:
        conn.close()



def add_schedule(start_time_iso: str, end_time_iso: str, mode: str, price: Optional[float] = None) -> bool:
    """
    Insert schedule if not exists (unique on start_time + end_time).
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO {DB_NAMESPACE} (start_time, end_time, mode, price_p_per_kwh) VALUES (?, ?, ?, ?)",
            (start_time_iso, end_time_iso, mode, price)
        )
        conn.commit()
        logging.info(f"Added schedule {start_time_iso} -> {end_time_iso} [{mode}] @ {price} p/kWh")
        return True
    except sqlite3.IntegrityError:
        logging.debug("Duplicate schedule detected; skipping insert.")
        return False
    except Exception as e:
        logging.error(f"Failed to add schedule: {e}")
        return False
    finally:
        conn.close()


def fetch_pending_schedules() -> List[Tuple]:
    """Fetch non-executed, non-expired schedules."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT id, start_time, end_time, mode, executed, created_at,last_retry_utc,
               retry_count, expired, decision,decision_at,price_p_per_kwh
        FROM {DB_NAMESPACE}
        WHERE executed = 0 AND (expired IS NULL OR expired = 0)
        ORDER BY start_time ASC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

# def fetch_schedules_for_execution():
#     """Fetch all schedules that are not executed (pending or expired)."""
#     conn = get_connection()
#     cur = conn.cursor()
#     cur.execute(f"""
#         SELECT id, start_time, end_time, mode, executed, last_retry_utc,
#                retry_count, price_p_per_kwh
#         FROM {DB_NAMESPACE}
#         WHERE executed = 0
#         ORDER BY start_time
#     """)
#     rows = cur.fetchall()
#     conn.close()
#     return rows

def update_schedule_price(schedule_id: int, price: float):
    """Update stored price for an existing schedule."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        UPDATE {DB_NAMESPACE}
        SET price_p_per_kwh = ?
        WHERE id = ?
    """, (price, schedule_id))
    conn.commit()
    conn.close()
    logging.info(f"Updated schedule {schedule_id} with price {price:.3f} p/kWh.")


def mark_as_executed(schedule_id: int,decision = 'executed'):
    """Mark schedule executed."""
    conn = get_connection()
    cur = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()

    executed_val = 1 if decision in ("executed", "completed","cancelled") else 0
    expired_val = 1 if decision == "expired" else 0

    cur.execute(f"""
        UPDATE {DB_NAMESPACE}
        SET executed = ?, expired = ?, decision = ?,
            decision_at = ?
        WHERE id = ?
    """, (executed_val, expired_val, decision,now_iso, schedule_id)
    )
    conn.commit()
    conn.close()
    logging.info(f"Schedule {schedule_id} marked as {decision}.")


def delete_schedule(schedule_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"DELETE FROM {DB_NAMESPACE} WHERE id = ?", (schedule_id,))
    conn.commit()
    conn.close()
    logging.info(f"Schedule {schedule_id} deleted.")

def mark_all_expired(now: datetime):
    """
    Mark all schedules whose end_time has passed as expired in both
    schedules and decisions tables.
    """
    conn = get_connection()
    cur = conn.cursor()

    try:
        # 1️⃣ Select all unexecuted, non-expired schedules whose end_time < now
        cur.execute(f"""
            SELECT id, start_time, end_time, mode, price_p_per_kwh
            FROM {DB_NAMESPACE}
            WHERE end_time < ?
              AND (executed IS NULL OR executed = 0)
              AND (expired IS NULL OR expired = 0)
        """, (now.isoformat(),))
        expired_rows = cur.fetchall()

        if not expired_rows:
            return 0  # nothing to mark

        # 2️⃣ Update all those schedules as expired
        cur.execute(f"""
            UPDATE {DB_NAMESPACE}
            SET expired = 1,
                decision = 'expired',
                decision_at = ?,
                executed = 0
            WHERE end_time < ?
              AND (executed IS NULL OR executed = 0)
              AND (expired IS NULL OR expired = 0)
        """, (now.isoformat(), now.isoformat()))

        # 3️⃣ Insert into decisions for each expired schedule
        for row in expired_rows:
            schedule_id, start_time, end_time, mode, price_p_per_kwh = row

            # Optional check to prevent duplicates
            cur.execute(f"""
                SELECT COUNT(1)
                FROM {DECISIONS_DB_TABLE}
                WHERE schedule_id = ? AND LOWER(action) = 'expired'
            """, (schedule_id,))
            already_logged = cur.fetchone()[0]

            if not already_logged:
                cur.execute(f"""
                    INSERT INTO {DECISIONS_DB_TABLE} (
                        schedule_id, start_time, end_time,
                        action, reason, soc, solar_power, island_status,
                        price_p_per_kwh, timestamp
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    schedule_id, start_time, end_time,
                    'expired', 'schedule_missed',
                    None, None, None,
                    price_p_per_kwh, now.isoformat()
                ))

        conn.commit()
        logging.info(f"🕒 Marked {len(expired_rows)} schedules as expired.")
        return len(expired_rows)

    except Exception as e:
        logging.error(f"Error marking expired schedules: {e}")
        conn.rollback()
        return 0

    finally:
        conn.close()

# -----------------------------
# Decisions (audit)
# -----------------------------
def add_decision(schedule_id: int, start_time_iso: str, end_time_iso: str,
                 action: str, reason: str, soc: Optional[float] = None,
                 solar_power: Optional[float] = None, island_status: Optional[str] = None,
                 price_p_per_kwh: Optional[float] = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        INSERT INTO {DECISIONS_DB_TABLE}
        (schedule_id, start_time, end_time, action, reason, soc, solar_power, island_status, price_p_per_kwh)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (schedule_id, start_time_iso, end_time_iso, action, reason, soc, solar_power, island_status, price_p_per_kwh))
    conn.commit()
    conn.close()
    logging.info(f"Decision logged for schedule {schedule_id}: {action} ({reason})")


def log_price_decision(schedule_id: int, start_time: str, end_time: str, price: float, avg_price: float):
    """Log decision for price-based cancellation."""
    reason = f"Price {price:.2f}p > threshold or average {avg_price:.2f}p"
    add_decision(schedule_id, start_time, end_time, "cancelled", reason, price_p_per_kwh=price)


def fetch_recent_decisions(limit: int = 50):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT id, timestamp, schedule_id, start_time, end_time,
               action, reason, soc, solar_power, island_status, price_p_per_kwh
        FROM {DECISIONS_DB_TABLE}
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


# -----------------------------
# Retry helpers
# -----------------------------
def get_last_retry(schedule_id: int) -> Optional[datetime]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT last_retry_utc FROM {DB_NAMESPACE} WHERE id = ?", (schedule_id,))
    row = cur.fetchone()
    conn.close()
    if row and row["last_retry_utc"]:
        try:
            return datetime.fromisoformat(row["last_retry_utc"])
        except Exception:
            return None
    return None


def update_last_retry(schedule_id: int):
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        UPDATE {DB_NAMESPACE}
        SET last_retry_utc = ?, retry_count = COALESCE(retry_count,0) + 1
        WHERE id = ?
    """, (now, schedule_id))
    conn.commit()
    conn.close()


def reset_retry(schedule_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        UPDATE {DB_NAMESPACE}
        SET last_retry_utc = NULL, retry_count = 0
        WHERE id = ?
    """, (schedule_id,))
    conn.commit()
    conn.close()


def increment_retry(schedule_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"UPDATE {DB_NAMESPACE} SET retry_count = COALESCE(retry_count,0) + 1 WHERE id = ?", (schedule_id,))
    conn.commit()
    conn.close()


def get_retry_count(schedule_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT retry_count FROM {DB_NAMESPACE} WHERE id = ?", (schedule_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["retry_count"]) if row and row["retry_count"] is not None else 0


# -----------------------------
# Utilities
# -----------------------------
def purge_old_executed(days: int = 7):
    """Delete executed schedules older than N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"DELETE FROM {DB_NAMESPACE} WHERE executed = 1 AND datetime(created_at) < ?", (cutoff.isoformat(),))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted:
        logging.info(f"Purged {deleted} executed schedule(s) older than {days} days.")


def show_schema():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name;")
    for r in cur.fetchall():
        logging.info(r["sql"])
    conn.close()
