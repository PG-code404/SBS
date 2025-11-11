from datetime import datetime
from zoneinfo import ZoneInfo
from config.config import TIMEZONE

def to_local(dt_str):
    """Convert UTC ISO string or datetime to local time (Europe/London)."""
    if not dt_str:
        return None
    try:
        # Accept both string and datetime
        if isinstance(dt_str, str):
            dt_utc = datetime.fromisoformat(dt_str)
        else:
            dt_utc = dt_str

        local_tz = ZoneInfo(TIMEZONE)
        return dt_utc.astimezone(local_tz).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt_str  # fallback if parsing fails


def to_utc(dt_value):
    if not dt_value:
        return None
    try:
        if isinstance(dt_value, str):
            dt_local = datetime.fromisoformat(dt_value)
        else:
            dt_local = dt_value

        # 🧠 If already timezone-aware, just convert directly
        if dt_local.tzinfo is not None:
            return dt_local.astimezone(ZoneInfo("UTC")).isoformat()

        local_tz = ZoneInfo(TIMEZONE)
        dt_local = dt_local.replace(tzinfo=local_tz)
        return dt_local.astimezone(ZoneInfo("UTC")).isoformat()
    except Exception:
        return dt_value
    
def dt_to_short(dt):
    """Format datetimes like '11 Nov, 19:00'."""
    if not dt:
        return ""
    # Handle string timestamps safely too
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt  # return unchanged if not a valid datetime string
    return dt.strftime("%d %b, %H:%M")