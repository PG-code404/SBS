import sqlite3

db = r"C:\Users\vichu\OneDrive\Documents\Projects\SBS\data\Force_Charging.db"
conn = sqlite3.connect(db)
cur = conn.cursor()

print("Running migration...")

cur.executescript("""
PRAGMA foreign_keys=off;

BEGIN TRANSACTION;

CREATE TABLE decisions_new (
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
);

INSERT INTO decisions_new
SELECT id, schedule_id, start_time, end_time, action, reason, soc, solar_power, island_status, price_p_per_kwh, timestamp
FROM decisions;

DROP TABLE decisions;
ALTER TABLE decisions_new RENAME TO decisions;

COMMIT;

PRAGMA foreign_keys=on;
""")

conn.commit()
conn.close()

print("✅ Migration complete")
