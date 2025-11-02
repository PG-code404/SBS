-- Begin migration
BEGIN TRANSACTION;

-- 1. Rename old table
ALTER TABLE schedules RENAME TO schedules_old;

-- 2. Create new table (fixed schema)
CREATE TABLE schedules (
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
    source TEXT DEFAULT 'Scheduler'
);

-- 3. Copy data from old → new (ensure column order matches)
INSERT INTO schedules (
    id, start_time, end_time, mode, executed, created_at,
    last_retry_utc, retry_count, expired, decision, decision_at,
    price_p_per_kwh, manual_override, target_soc, source
)
SELECT
    id, start_time, end_time, mode, executed, created_at,
    last_retry_utc, retry_count, expired, decision, decision_at,
    price_p_per_kwh, manual_override, target_soc, source
FROM schedules_old;

-- 4. Restore indexes
-- Auto-detect indexes that belonged to schedules and re-create them
SELECT sql FROM sqlite_master
WHERE type = 'index' AND tbl_name = 'schedules_old';

-- Copy each index CREATE statement shown above and manually run it, example:
-- CREATE INDEX idx_schedules_start_time ON schedules(start_time);
-- CREATE INDEX idx_schedules_end_time ON schedules(end_time);
-- (Run all found indexes before continuing)

-- 5. Restore triggers
SELECT sql FROM sqlite_master
WHERE type = 'trigger' AND tbl_name = 'schedules_old';

-- Copy and run each trigger CREATE statement shown above

-- 6. Drop old table now that migration is safe
DROP TABLE schedules_old;

-- Done
COMMIT;

PRAGMA table_info(schedules);


