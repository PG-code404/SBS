# ⚡ Smart Battery Scheduler for Tesla Powerwall  
### Integrating Solar Forecasting, NetZero API, and Octopus Agile Dynamic Tariffs  

**Author:** Pradeep Ganesan  
**Co-author:** ChatGPT  
**License:** Personal and Non-Commercial Use Only  

---

## 🧭 Introduction

**Smart Battery Scheduler for Tesla Powerwall** is an intelligent home energy management system designed to optimize battery charging and grid usage.  
It integrates **Octopus Agile** dynamic tariffs, **NetZero API** for Tesla Powerwall control, and **solar forecasting** to schedule charging during the cheapest and cleanest energy periods.  

The system runs autonomously with a **Flask web dashboard** for monitoring and control, **Google OAuth** authentication, and **automated scheduling logic** using **real-time data**.

---

## 🌞 Key Features

- 🔋 **Automated Tesla Powerwall charge scheduling** based on Octopus Agile rates  
- ☀️ **Solar generation forecasting** for predictive energy management  
- 🌐 **Integration with NetZero API** for direct Powerwall control  
- 🕒 **Dynamic scheduler** that refreshes every 15 minutes or on event triggers  
- 🧭 **Manual overrides** for custom charge slots via dashboard  
- 🔒 **Secure Google OAuth 2.0 authentication** for user login  
- 📊 **Interactive dashboard** for viewing battery status, SoC, schedule, and solar generation  
- 🧩 **Simulation mode** for testing without API calls  
- 🧠 **Thread-safe scheduler loop** using Python events and background workers  

---

## 📁 Project Structure

project_root/
├── main.py # Core execution logic and executor status tracking
├── src/
│ ├── ScheduleChargeSlots.py # Fetches Agile rates and generates optimal schedules
│ ├── Keep_Alive.py # Flask web dashboard and OAuth authentication
│ ├── netzero_api.py # Interface to NetZero/Tesla Powerwall API
│ ├── db.py # Database interaction (SQLite)
│ ├── events.py # Global threading event synchronization
│ ├── timezone_utils.py # Timezone conversions and formatting
│ ├── SolarData.py # (Optional) Solar forecasting logic
│ ├── Octopus_saving_sessions.py # (Optional) Additional tariff management
│ └── config/
│ └── config.py # Configuration constants and URLs
├── templates/
│ ├── dashboard.html # Main control and status view
│ └── login.html # Google login page
├── static/ # CSS, JS, and images
├── requirements.txt # Python dependencies
└── README.md

## ⚙️ Installation
### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/smart-battery-scheduler.git
cd smart-battery-scheduler

**2. Create and activate a virtual environment**
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

**3. Install dependencies**

pip install -r requirements.txt

** 🔧 Configuration**
1. Environment Variables (.env)

Create a .env file in the project root with the following contents:

# --- Flask Web Server ---
FLASK_SECRET_KEY=your_flask_secret
KEEP_ALIVE_API_KEY=your_internal_api_key

# --- Google OAuth Setup ---
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
AUTHORIZED_EMAILS=you@example.com,another@example.com
FLASK_ENV=production  # or development

# --- NetZero / Powerwall Integration ---
NETZERO_API_KEY=your_netzero_api_key
SITE_ID=your_site_id
NETZERO_URL_TEMPLATE=https://api.nzero.io/v1/site/{SITE_ID}/settings

# --- Octopus Agile Tariffs ---
AGILE_URL=https://api.octopus.energy/v1/products/AGILE-xx-xx-xx/electricity-tariffs/E-1R-AGILE-xx-xx-xx-1/capacity

# --- Simulation ---
SIMULATION_MODE=False

# --- Timezone & Scheduling ---
TIMEZONE=Europe/London
RECOMMENDED_SLOTS=5
BATTERY_KWH=13.5
CHARGE_RATE_KW=3.5
SLOT_HOURS=0.5
TARGET_SOC=98
BATTERY_RESERVE_START=20


🧠 You can adjust SIMULATION_MODE=True for testing without sending API commands.

2. config/config.py

The configuration module defines constants such as:

AGILE_URL = "https://api.octopus.energy/v1/products/.../electricity-tariffs/..."
NETZERO_URL_TEMPLATE = "https://api.nzero.io/v1/site/{SITE_ID}/settings"
TIMEZONE = "Europe/London"
BATTERY_KWH = 13.5
CHARGE_RATE_KW = 3.5
SLOT_HOURS = 0.5
TARGET_SOC = 98

**🚀 Usage**
1. Run the scheduler and dashboard
python main.py

This will:

Start the background scheduler loop to fetch Agile prices

Launch the Flask dashboard (default port 8080)

Keep your scheduler and executor running continuously

Access the web interface at:
👉 http://localhost:8080
 (development)
or your deployed Cloud Run URL.

**🌐 API Endpoints**
Endpoint	Method	Description	Auth
/	GET	Login page or redirect to dashboard	Public
/login	GET	Initiates Google OAuth login	Public
/dashboard	GET	Main dashboard view	Login required
/putSchedule	POST	Add manual schedule	Login required
/getPendingSchedules	GET	Retrieve upcoming charge slots	Login required
/delSchedule/<id>	DELETE	Delete a scheduled slot	Login required
/status	GET	Returns executor and scheduler status	API key or login
/update_status	POST	Update system status (used by scheduler)	Internal
/health	GET	Health check endpoint	Public

**🧩 Integration Overview**
Integration	Purpose
Octopus Agile API	Fetches half-hourly dynamic energy tariffs to find cheapest charging slots
NetZero API	Controls Tesla Powerwall charge/discharge settings
Solar Forecasting	Predicts available solar generation to minimize grid import
Flask Dashboard	Provides monitoring and manual scheduling UI
SQLite Database	Stores generated and manual schedules persistently

**🧠 Scheduler Logic**
1. Fetch Agile tariff data
2. Parse and convert to local timezone
3. Select cheapest upcoming slots (nsmallest(RECOMMENDED_SLOTS))
4. Add new entries to the database
5. Trigger scheduler_refresh_event to update execution queue
6. Executor updates Powerwall settings via NetZero API

**🐞 Troubleshooting**
Issue	Possible Cause	Solution
Google OAuth login fails	Incorrect redirect URI	Ensure OAuth consent screen allows your domain
Scheduler not updating	Event not triggered	Restart main.py or manually trigger /update_status
Flask app won’t start	Port conflict or missing .env	Check port 8080 or environment configuration
NetZero API errors	Invalid token or endpoint	Verify NETZERO_API_KEY and SITE_ID

**🧑‍💻 Development Notes**

Built with Python 3.10+

Uses Flask, Authlib, Flask-Login, Pandas, and Waitress

Threaded scheduler ensures smooth async background operation

Designed for deployment on Google Cloud Run or local Raspberry Pi

👥 Contributors

Pradeep Ganesan — Lead Developer & System Architect

ChatGPT — Co-author & Documentation Assistance

📜 License

© 2025 Pradeep Ganesan. All Rights Reserved.
This project is provided for personal and non-commercial use only.
You may modify or use this software for private educational or home automation purposes.
Commercial redistribution, resale, or SaaS deployment is strictly prohibited without explicit permission from the author.


**"Empowering sustainable energy decisions through smart automation." ⚡**
