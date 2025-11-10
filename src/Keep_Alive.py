# Keep_Alive.py
import os
import logging
import threading
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, request, jsonify, redirect, url_for, session, render_template
from authlib.integrations.flask_client import OAuth
from authlib.integrations.base_client.errors import MismatchingStateError

from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from waitress import serve

from src.ScheduleChargeSlots import add_manual_charge_schedule, scheduler_loop, scheduler_refresh_event
from src.events import executor_wake_event
from src.db import fetch_pending_schedules, remove_schedule
from src.timezone_utils import to_local
import main

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KeepAlive")

# --- Config ---
KEEP_ALIVE_API_KEY = os.getenv("KEEP_ALIVE_API_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", os.urandom(32).hex())
AUTHORIZED_EMAILS = [
    e.strip() for e in os.getenv("AUTHORIZED_EMAILS", "").split(",")
    if e.strip()
]

# --- Flask setup ---
app = Flask(__name__,
            static_folder=os.path.join(os.getcwd(), "static"),
            template_folder=os.path.join(os.getcwd(), "templates"))
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret")
"""
# Determine environment
FLASK_ENV = os.getenv("FLASK_ENV", "production").lower()  # "development" or "production"

if FLASK_ENV == "development":
    # Local testing over HTTP
    app.config['SESSION_COOKIE_SECURE'] = False
else:
    # Production: enforce HTTPS
    app.config['SESSION_COOKIE_SECURE'] = True

app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
"""

#app.config['SESSION_COOKIE_SECURE'] = False
#app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
#app.config['SESSION_COOKIE_HTTPONLY'] = True
#app.logger.setLevel(logging.DEBUG)

oauth = OAuth(app)
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    google = oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        access_token_url="https://oauth2.googleapis.com/token",
        authorize_url="https://accounts.google.com/o/oauth2/auth",
        api_base_url="https://www.googleapis.com/oauth2/v2/",
        userinfo_endpoint="https://www.googleapis.com/oauth2/v2/userinfo",
        server_metadata_url=
        'https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={"scope": "openid email profile"},
    )
else:
    google = None
    logger.info("⚠️ Google OAuth not configured. Login will be disabled.")

login_manager = LoginManager(app)
login_manager.login_view = "home"  # type: ignore[assignment]

def get_redirect_uri():
    if os.getenv("FLASK_ENV") == "development":
        return url_for("auth_callback", _external=True)
    else:
        # Use Cloud Run public URL in production
        return url_for("auth_callback", _external=True, _scheme="https")


class SimpleUser(UserMixin):

    def __init__(self, userdata):
        self.id = userdata.get("email")
        self.userdata = userdata


@login_manager.user_loader
def load_user(user_id):
    u = session.get("user")
    if u and u.get("email") == user_id:
        return SimpleUser(u)
    return None


# -----------------------------
# Executor State
# -----------------------------

last_scheduler_run = main.EXECUTOR_STATUS["last_scheduler_run"]
executor_status = main.EXECUTOR_STATUS["message"]
next_schedule_time = main.EXECUTOR_STATUS["next_schedule_time"]


# -----------------------------
# Helpers
# -----------------------------
def allow_internal_or_logged_in(func):

    @wraps(func)
    def wrapper(*args, **kwargs):
        if current_user and getattr(current_user, "is_authenticated", False):
            return func(*args, **kwargs)
        remote = request.remote_addr or ""
        if remote in ("127.0.0.1", "::1", "localhost","https://agileoctopw-697014942939.europe-west1.run.app"):
            return func(*args, **kwargs)
        key = request.headers.get("x-api-key") or request.args.get("api_key")
        if key and KEEP_ALIVE_API_KEY and key == KEEP_ALIVE_API_KEY:
            return func(*args, **kwargs)
        print(f"[Auth] ❌ Unauthorized request from {remote} with key={key}")
        return jsonify({"error": "Unauthorized"}), 401

    return wrapper


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def home():
    if not google:
        return "<p>Google login not configured. Visit /status</p>"
    if "user" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/login")
def login():
    if not google:
        return "Google OAuth not configured", 503
    
    # Always regenerate a fresh unique state value and persist it in session
    #state = os.urandom(16).hex()
    #session['oauth_state'] = state
    
    
    #redirect_uri = get_redirect_uri()
    redirect_uri = url_for("auth_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/callback")
def auth_callback():
    if not google:
        return "Google OAuth not configured", 503
   
    try:
        #stored_state = session.get("oauth_state")
        #returned_state = request.args.get("state")
        token = google.authorize_access_token()
        
        # Prevent mismatched or expired session issues
        #if not stored_state or stored_state != returned_state:
            #session.pop("oauth_state", None)
            #return redirect(url_for("login"))
         
        user_info = google.get("userinfo").json()
        email = user_info.get("email")
        if AUTHORIZED_EMAILS and email not in AUTHORIZED_EMAILS:
            logger.warning(f"Unauthorized email login: {email}")
            return "❌ Unauthorized — contact admin.", 403
        
        session["user"] = user_info
        login_user(SimpleUser(user_info))
        #session.modified = True
        print("Session keys before callback:", session.keys())
        return redirect(url_for("dashboard"))
    
    except MismatchingStateError as e:
        # Common cause: user switched devices or browser tabs mid-login
        logger.warning(f"OAuth state mismatch — possible CSRF/session timeout: {e}")
        session.clear()
        # 👇 Instead of internal error, clean redirect
        return redirect(url_for("login", expired=1))
    
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return jsonify({
            "error": "OAuth login failed",
            "message": str(e)
        }), 500


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    logout_user()
    return redirect("/")


@app.route("/dashboard")
@login_required
def dashboard():
    user = session.get("user", {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    now_dt = datetime.now()
    uptime = str(
        timedelta(seconds=int((now_dt -
                               main.PROCESS_START_TIME).total_seconds())))

    # Read all fields from EXECUTOR_STATUS
    executor_status_msg = main.EXECUTOR_STATUS.get("message")
    last_run = main.EXECUTOR_STATUS.get("last_scheduler_run")
    if isinstance(last_run, str):
        try:
            last_run = datetime.fromisoformat(last_run)
        except ValueError:
            last_run = None
    next_schedule = main.EXECUTOR_STATUS.get("next_schedule_time")

    last_run_str = last_run.strftime(
        "%Y-%m-%d %H:%M:%S") if last_run else "Not yet run"
    next_schedule_time = next_schedule if next_schedule else "Pending"

    return render_template(
        "dashboard.html",
        user=user,
        now=now,
        executor_status_msg=executor_status_msg,
        last_scheduler_run=last_run_str,
        next_schedule_time=next_schedule_time,
        uptime=uptime,
        active_schedule_id=main.EXECUTOR_STATUS.get("active_schedule_id"),
        current_price=main.EXECUTOR_STATUS.get("current_price"),
        soc=main.EXECUTOR_STATUS.get("soc"),
        solar_power=main.EXECUTOR_STATUS.get("solar_power"),
        island=main.EXECUTOR_STATUS.get("island"),
    )


@app.route("/putSchedule", methods=["POST"])
@login_required
def add_schedule():
    """Add a manual schedule entry into the database."""
    #from src.db import add_manual_override
    data = request.get_json()
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    target_soc = data.get("target_soc", 98)

    try:
        # Optional: validate datetime formats
        datetime.strptime(start_time, "%Y-%m-%dT%H:%M")
        datetime.strptime(end_time, "%Y-%m-%dT%H:%M")

        # Call your DB helper to add manual schedule
        add_manual_charge_schedule(start_time, end_time, target_soc)
        #scheduler_refresh_event.set()
        executor_wake_event.set()
        #logging.info(f"[{__name__}] Event object ID: {id(executor_wake_event)}  (Set? {executor_wake_event.is_set()})  Thread: {threading.current_thread().name}")
        return jsonify({"message": "Manual schedule added successfully!"})
    except Exception as e:
        logging.info("Error adding schedule:", e)
        logging.info(f"{start_time}, {end_time}, {target_soc}")
        return jsonify({"message": f"Failed to add schedule: {e}"}), 500


@app.route("/getPendingSchedules")
@login_required
def pending_schedules():
    """Return all pending schedules (non-executed, non-expired)."""
    rows = fetch_pending_schedules(
    )  # make sure this sets row_factory = sqlite3.Row

    schedules = [{
        "id":r["id"],  # type: ignore[index]
        "start_time":to_local(r["start_time"]),  # type: ignore[index]
        "end_time":to_local(r["end_time"]),  # type: ignore[index]
        "target_soc":r["target_soc"] if r["target_soc"] is not None else '-',  # type: ignore[index]
        "price_p_per_kwh":r["price_p_per_kwh"] if r["price_p_per_kwh"] is not None else '-',  # type: ignore[index]
        "manual_override":bool(r["manual_override"])  # type: ignore[index] 
        if r["manual_override"] is not None else False,  # type: ignore[index]
        "source":r["source"] if r["source"] else "scheduler",  # type: ignore[index]
        "mode":r["mode"] if r["mode"] else "autonomous",  # type: ignore[index]
    } for r in rows]
    return jsonify(schedules)


# -----------------------------
# System endpoints
# -----------------------------


@app.route("/status", methods=["GET"])
@allow_internal_or_logged_in
def get_status():
    from datetime import datetime
    from main import PROCESS_START_TIME

    return jsonify({
        "executor_status_msg":
        main.EXECUTOR_STATUS.get("message"),
        "last_scheduler_run":datetime.fromisoformat(main.EXECUTOR_STATUS["last_scheduler_run"]).strftime("%Y-%m-%d %H:%M:%S")
        if main.EXECUTOR_STATUS.get("last_scheduler_run") else "Not yet run",
        "next_schedule_time":main.EXECUTOR_STATUS.get("next_schedule_time"),
        "active_schedule_id":main.EXECUTOR_STATUS.get("active_schedule_id"),
        "current_price":main.EXECUTOR_STATUS.get("current_price"),
        "soc":main.EXECUTOR_STATUS.get("soc"),
        "solar_power":main.EXECUTOR_STATUS.get("solar_power"),
        "island":main.EXECUTOR_STATUS.get("island"),
        "uptime": (datetime.now() - PROCESS_START_TIME).total_seconds(),
    })


@app.route("/update_status", methods=["POST"])
@allow_internal_or_logged_in
def update_status():
    """
    Receives executor status updates from the scheduler/executor
    and updates the shared EXECUTOR_STATUS dictionary.
    """
    data = request.json
    if not data:
        return jsonify({"error": "No JSON payload provided"}), 400

    # Update EXECUTOR_STATUS with all keys sent
    for key, value in data.items():
        main.EXECUTOR_STATUS[key] = value
    #logging.info(f"/update_status: {data}")
    return jsonify({"status": "ok"})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


@app.route("/delSchedule/<int:schedule_id>", methods=["DELETE"])
@login_required
def delete_schedule(schedule_id: int):
    """
    Delete a schedule by ID and log a 'deleted' decision.
    Returns JSON response for JS.
    """
    try:
        # Call your DB function to delete schedule
        remove_schedule(schedule_id)
        logging.info(f"Schedule {schedule_id} deleted.")
        scheduler_refresh_event.set()
        executor_wake_event.set()
        return jsonify({
            "status": "ok",
            "message": f"Schedule {schedule_id} is deleted"
        })
    except Exception as e:
        logging.error(f"Error deleting schedule {schedule_id}: {e}")
        return jsonify({"status": "error", "message": "System Error"}), 500


# -----------------------------
# Server runner
# -----------------------------
def _run_server():
    from waitress import serve

    PORT = int(os.environ.get("PORT", 8080))  # Cloud Run sets $PORT
    serve(app, host="0.0.0.0", port=PORT)

def keep_alive():
    threading.Thread(target=_run_server, daemon=True).start()
    #threading.Thread(target=scheduler_loop, daemon=True).start()
    logger.info("Keep-alive server started.")
