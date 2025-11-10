import threading

executor_wake_event = threading.Event()
scheduler_refresh_event = threading.Event()