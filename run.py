#!/usr/bin/env python3
"""
run.py - Main entry point for the Battery Charging Scheduler application.
This script starts both the Flask web server and the main executor.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    from src.Keep_Alive import keep_alive
    import main
    
    print("=" * 60)
    print("Battery Charging Scheduler - Starting...")
    print("=" * 60)
    
    port = os.getenv("KEEP_ALIVE_PORT", "5000")
    print(f"Flask web server will start on http://0.0.0.0:{port}")
    print("Main executor/scheduler will start in parallel")
    print("=" * 60)
    
    keep_alive()
    
    try:
        main.main()
    except KeyboardInterrupt:
        print("\n\nShutdown requested... exiting gracefully")
        sys.exit(0)
