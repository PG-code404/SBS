# db.py
import sqlite3
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple
import pytz

from config.config import DB_PATH, DB_NAMESPACE, DECISIONS_DB_TABLE, TIMEZONE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOCAL_TZ = pytz.timezone(TIMEZONE)

# -----------------------------
# DB Connection
# -----------------------------
def get_connection(timeout: int = 30):
    """
    Return a SQLite connection with WAL enabled and a row factory.
    Note: callers that open a connection should close it.
    """
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES, timeout=timeout, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.row_factory = sqlite3.Row
    return conn

# -----------------------------
# Threading + safe execute
# -----------------------------
db_lock = threading.RLock()

def safe_execute(sql: str, params: tuple = (), commit: bool = True, retries: int = 5, backoff: float = 0.25):
    """
    Execute a SQL statement in a thread-safe way with retries on 'database is locked'.
    - Opens & closes connection internally (unless you pass a connection object in future).
    - Returns the cursor when successful (cursor is from the connection; note connection closed on return).
    Raises RuntimeError on repeated lock failures.
    """
    #print(sql)
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            with db_lock:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(sql, params)
                if commit:
                    conn.commit()
                # To return rows we need to fetch before closing if it's a SELECT,
                # but here safe_execute is primarily used for writes/DDL/UPDATE/INSERT.
                # Return a small helper object with cursor info; close conn afterwards.
                # If caller needs fetchall, they should use get_connection/read path.
                cur_id = getattr(cur, "lastrowid", None)
                cur_rowcount = getattr(cur, "rowcount", None)
                conn.close()
                class _Res:
                    lastrowid = cur_id
                    rowcount = cur_rowcount
                return _Res()
        except sqlite3.OperationalError as e:
            last_exc = e
            if "locked" in str(e).lower():
                logging.warning(f"DB locked, retrying ({attempt}/{retries})...")
                time.sleep(backoff * attempt)
                continue
            raise
        except Exception:
            raise
    raise RuntimeError(f"Failed to execute DB query after {retries} retries: {last_exc}")

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
    Non-destructive: uses CREATE TABLE IF NOT EXISTS and ALTER TABLE ADD COLUMN for missing fields.
    """
    # Create base table if missing
    sql = f"""
    CREATE TABLE IF NOT EXISTS {DB_NAMESPACE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        mode TEXT DEFAULT 'autonomous',
        executed INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """
    safe_execute(sql, (), commit=True)

    # Add optional columns if missing
    existing = _table_columns(DB_NAMESPACE)
    optional_columns = {
        "last_retry_utc": "TEXT DEFAULT NULL",
        "retry_count": "INTEGER DEFAULT 0",
        "expired": "INTEGER DEFAULT 0",
        "decision": "TEXT DEFAULT NULL",
        "decision_at": "TEXT DEFAULT NULL",
        "price_p_per_kwh": "REAL DEFAULT NULL",
        "manual_override": "INTEGER DEFAULT 0",
        "target_soc": "INTEGER DEFAULT 0",
        "source": "TEXT DEFAULT 'scheduler'",
    }

    for col, col_def in optional_columns.items():
        if col not in existing:
            logging.info(f"Adding missing column '{col}' to {DB_NAMESPACE}")
            safe_execute(f"ALTER TABLE {DB_NAMESPACE} ADD COLUMN {col} {col_def};", ())

    # Unique index on (start_time, end_time)
    try:
        safe_execute(f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{DB_NAMESPACE}_start_end
            ON {DB_NAMESPACE} (start_time, end_time)
        """, ())
    except Exception as e:
        logging.warning(f"Could not create unique index idx_{DB_NAMESPACE}_start_end: {e}")

    # Ensure decisions table
    safe_execute(f"""
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
    """, ())

def init_db():
    """Initialize DB and ensure schema is up-to-date."""
    _ensure_columns()
    logging.info("DB initialized and schema ensured.")

# -----------------------------
# Schedules helpers
# -----------------------------

import threading
_db_write_lock = threading.RLock()

def add_schedules_batch(schedules: list) -> int:
    """
    Insert multiple schedules in a single transaction.
    Returns the number of successfully inserted schedules.
    """
    inserted = 0
    conn = get_connection()
    try:
        with _db_write_lock:
            cur = conn.cursor()
            for sched in schedules:
                try:
                    cur.execute(f"""
                        INSERT INTO {DB_NAMESPACE} (start_time, end_time, mode, target_soc, price_p_per_kwh)
                        VALUES (?, ?, ?, ?, ?)
                    """, sched)
                    inserted += 1
                    logging.info(f"Saved schedule: [{sched[0]}] -> [{sched[1]}] {sched[3]}% @ {sched[4]} p/kWh")
                except sqlite3.IntegrityError:
                    logging.info(f"⚠️ Duplicate skipped: {sched[0]}")
            conn.commit()
    finally:
        conn.close()
    return inserted


def add_schedule(start_time_iso: str, end_time_iso: str, mode: str = "autonomous", price: Optional[float] = None) -> bool:
    """
    Insert schedule if not exists (unique on start_time + end_time).
    Returns True if inserted, False if duplicate or failed.
    """
   
    try:
        sql = f"INSERT INTO {DB_NAMESPACE} (start_time, end_time, mode, price_p_per_kwh) VALUES (?, ?, ?, ?)"
        safe_execute(sql, (start_time_iso, end_time_iso, mode, price))
        logging.info(f"Added schedule {start_time_iso} -> {end_time_iso} [{mode}] @ {price} p/kWh")
        return True
    except sqlite3.IntegrityError:
        logging.debug("Duplicate schedule detected; skipping insert.")
        return False
    except Exception as e:
        logging.error(f"Failed to add schedule: {e}")
        return False

def fetch_pending_schedules() -> List[Tuple]:
    """Fetch non-executed, non-expired schedules as sqlite3.Row objects."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT id, start_time, end_time, mode, executed, created_at, last_retry_utc,
               retry_count, expired, decision, decision_at, price_p_per_kwh,
               target_soc, manual_override, source
        FROM {DB_NAMESPACE}
        WHERE executed = 0 AND (expired IS NULL OR expired = 0)
        ORDER BY start_time ASC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

def update_schedule_price(schedule_id: int, price: float) -> bool:
    try:
        sql = f"UPDATE {DB_NAMESPACE} SET price_p_per_kwh = ? WHERE id = ?"
        safe_execute(sql, (price, schedule_id))
        logging.info(f"Updated schedule {schedule_id} with price {price:.3f} p/kWh.")
        return True
    except Exception as e:
        logging.error(f"Failed to update schedule price: {e}")
        return False

def mark_as_executed(schedule_id: int, decision: str = 'executed'):
    """
    Mark schedule executed/expired/cancelled.
    decision: string used for auditing
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    executed_val = 1 if decision in ("executed", "completed", "cancelled") else 0
    expired_val = 1 if decision == "expired" else 0

    sql = f"""
        UPDATE {DB_NAMESPACE}
        SET executed = ?, expired = ?, decision = ?, decision_at = ?
        WHERE id = ?
    """
    safe_execute(sql, (executed_val, expired_val, decision, now_iso, schedule_id))
    logging.info(f"Schedule {schedule_id} marked as {decision}.")

def remove_schedule(schedule_id: int) -> bool:
    try:
        conn = get_connection()
        cur = conn.cursor()
        sql = f"DELETE FROM {DB_NAMESPACE} WHERE id = ?"
        safe_execute(sql, (schedule_id,))
        logging.info(f"Schedule {schedule_id} deleted.")
        
        add_decision(schedule_id, None, None, "deleted", "Deleted by User")
    
        conn.commit()
        conn.close()

    except Exception as e:
        logging.error(f"Failed to delete schedule {schedule_id}: {e}")

def mark_all_expired(now: datetime) -> int:
    """
    Mark all schedules whose end_time has passed as expired (non-destructive).
    Returns number of expired rows processed.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            SELECT id, start_time, end_time, mode, price_p_per_kwh
            FROM {DB_NAMESPACE}
            WHERE end_time < ?
              AND (executed IS NULL OR executed = 0)
              AND (expired IS NULL OR expired = 0)
        """, (now.isoformat(),))
        expired_rows = cur.fetchall()
        if not expired_rows:
            conn.close()
            return 0

        # Update expired flag
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

        # Insert decision records (avoid duplicates)
        for row in expired_rows:
            schedule_id, start_time, end_time, mode, price_p_per_kwh = row
            cur.execute(f"""
                SELECT COUNT(1) FROM {DECISIONS_DB_TABLE}
                WHERE schedule_id = ? AND LOWER(action) = 'expired'
            """, (schedule_id,))
            already_logged = cur.fetchone()[0]
            if not already_logged:
                cur.execute(f"""
                    INSERT INTO {DECISIONS_DB_TABLE} (
                        schedule_id, start_time, end_time,
                        action, reason, soc, solar_power, island_status,
                        price_p_per_kwh, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    schedule_id, start_time, end_time,
                    'expired', 'schedule_missed',
                    None, None, None,
                    price_p_per_kwh, now.isoformat()
                ))
        conn.commit()
        logging.info(f"Marked {len(expired_rows)} schedules as expired.")
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
def add_decision(schedule_id: int, start_time_iso: Optional[str], end_time_iso: Optional[str],
                 action: str, reason: str, soc: Optional[float] = None,
                 solar_power: Optional[float] = None, island_status: Optional[str] = None,
                 price_p_per_kwh: Optional[float] = None):
    try:
        sql = f"""
            INSERT INTO {DECISIONS_DB_TABLE}
            (schedule_id, start_time, end_time, action, reason, soc, solar_power, island_status, price_p_per_kwh)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        safe_execute(sql, (schedule_id, start_time_iso, end_time_iso, action, reason,
                           soc, solar_power, island_status, price_p_per_kwh))
        logging.info(f"Decision logged for schedule {schedule_id}: {action} ({reason})")
    except Exception as e:
        logging.error(f"Failed to log decision for {schedule_id}: {e}")

def log_price_decision(schedule_id: int, start_time: str, end_time: str, price: float, avg_price: float):
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
# Retry helpers for schedule attempts
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
    sql = f"UPDATE {DB_NAMESPACE} SET last_retry_utc = ?, retry_count = COALESCE(retry_count,0) + 1 WHERE id = ?"
    safe_execute(sql, (now, schedule_id))

def reset_retry(schedule_id: int):
    sql = f"UPDATE {DB_NAMESPACE} SET last_retry_utc = NULL, retry_count = 0 WHERE id = ?"
    safe_execute(sql, (schedule_id,))

def increment_retry(schedule_id: int):
    sql = f"UPDATE {DB_NAMESPACE} SET retry_count = COALESCE(retry_count,0) + 1 WHERE id = ?"
    safe_execute(sql, (schedule_id,))

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
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = f"DELETE FROM {DB_NAMESPACE} WHERE executed = 1 AND datetime(created_at) < ?"
    safe_execute(sql, (cutoff.isoformat(),))
    logging.info(f"Purged executed schedules older than {days} days.")

def show_schema():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name;")
    for r in cur.fetchall():
        logging.info(r["sql"])
    conn.close()

def add_manual_override(start_time_iso: str, end_time_iso: str, target_soc: int = 98) -> bool:
    """
    Add a manual charge schedule that bypasses normal checks.
    Uses safe_execute (thread-safe + retries).
    """
    try:
        sql = f"""
            INSERT INTO {DB_NAMESPACE} (start_time, end_time, target_soc, source, manual_override, executed, mode)
            VALUES (?, ?, ?, 'manual', 1, 0, 'manual')
        """
        safe_execute(sql, (start_time_iso, end_time_iso, int(target_soc)))
        logging.info(f"Manual schedule added: {start_time_iso} → {end_time_iso}, target SOC: {target_soc}% (manual override)")
        return True
    except sqlite3.IntegrityError:
        logging.debug("Duplicate manual schedule detected; skipping insert.")
        return False
    except Exception as e:
        logging.error(f"Failed to add manual override: {e}")
        return False

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

# -----------------------------
# Initialize DB when module imported directly
# -----------------------------
if __name__ == "__main__":
    init_db()
    logging.info("DB ready.")
