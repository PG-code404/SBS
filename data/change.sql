BEGIN TRANSACTION;

CREATE TABLE schedules_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    mode TEXT DEFAULT 'autonomous',
    executed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_retry_utc TEXT DEFAULT NULL,
    retry_count INTEGER DEFAULT 0,
    expired INTEGER DEFAULT 0,
    decision TEXT DEFAULT NULL,
    decision_at TEXT DEFAULT NULL,
    price_p_per_kwh REAL DEFAULT NULL,
    manual_override INTEGER DEFAULT 0,
    target_soc INTEGER DEFAULT 0,
    source TEXT DEFAULT 'scheduler',
);

INSERT INTO schedules_new
SELECT id, start_time, end_time, mode, executed, created_at, last_retry_utc,
       retry_count, expired, decision, decision_at, price_p_per_kwh,
       manual_override, target_soc, source
FROM schedules;

DROP TABLE schedules;
ALTER TABLE schedules_new RENAME TO schedules;

COMMIT;

