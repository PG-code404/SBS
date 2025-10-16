from flask import Flask, request
from threading import Thread

app = Flask(__name__)

@app.route('/')
def home():
    return "...Smart Battery Scheduler is running..."

@app.route('/shutdown')
def shutdown():
    func = request.environ.get('werkzeug.server.shutdown')
    if func:
        func()
    return "Shutting down..."

def run():
    app.run(host='0.0.0.0', port=8080, debug=False)

def keep_alive():
    """Run Flask server in background thread"""
    t = Thread(target=run, daemon=True)
    t.start()
