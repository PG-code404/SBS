# Battery Charging Scheduler

## Overview
A Python Flask web application that intelligently manages battery charging schedules based on:
- Octopus Energy Agile tariff pricing
- Solar generation forecasts
- Battery state of charge (SOC)
- NetZero battery API integration

The system automatically schedules charging during cheap electricity periods while avoiding peak times and leveraging solar generation when available.

## Recent Changes
- **2025-11-05**: Project imported to Replit
  - Configured for port 5000 (Replit requirement)
  - Updated dependencies installation
  - Created .env.example with all configuration options
  - Created run.py entry point script
  - Updated .gitignore for Python environment
  - Secured /update_status endpoint with authentication
  - Configured deployment for VM target (stateful application)
  - Verified application runs successfully with all integrations

## Project Architecture

### Core Components
1. **Flask Web Server** (`src/Keep_Alive.py`)
   - Dashboard UI with Google OAuth authentication
   - REST API endpoints for schedule management
   - Status monitoring and updates
   - Runs on port 5000 (configurable via KEEP_ALIVE_PORT)

2. **Main Executor** (`main.py`)
   - Continuously monitors and executes charging schedules
   - Checks battery status via NetZero API
   - Fetches Agile pricing from Octopus Energy
   - Implements safety features (peak avoidance, price limits, off-grid detection)

3. **Scheduler** (`src/ScheduleChargeSlots.py`)
   - Generates optimal charging schedules
   - Runs multiple times per day (configurable)
   - Considers solar forecasts and pricing

### Key Dependencies
- Flask 3.1.2 (web framework)
- Waitress (production WSGI server)
- authlib (Google OAuth)
- pandas (data processing)
- requests (API calls)
- openmeteo-requests (solar forecast)

### Database
- SQLite database at `data/Force_Charging.db`
- Stores schedules and decision history
- Auto-initialized on first run

## Configuration

### Required Environment Variables
These must be set via Replit Secrets:
- `NETZERO_API_KEY` - NetZero battery API key
- `SITE_ID` - NetZero site identifier
- `OCTOPUS_API_KEY` - Octopus Energy API key
- `GOOGLE_CLIENT_ID` - Google OAuth client ID
- `GOOGLE_CLIENT_SECRET` - Google OAuth secret
- `AUTHORIZED_EMAILS` - Comma-separated list of allowed login emails
- `FLASK_SECRET_KEY` - Flask session secret
- `KEEP_ALIVE_API_KEY` - Internal API authentication key

### Optional Configuration
See `.env.example` for all available options including:
- Battery specifications (capacity, charge rate, reserve levels)
- Scheduling parameters (lookahead, intervals)
- Solar panel configuration
- Price thresholds
- Time windows for peak avoidance

## Running the Application
The application is started via `run.py` which:
1. Loads environment variables from .env
2. Starts the Flask web server on port 5000
3. Starts the scheduler/executor in parallel threads

## User Preferences
None yet - first time setup in progress.
