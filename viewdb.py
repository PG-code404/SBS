import sqlite3
from config import DB_PATH, DB_NAMESPACE

# Connect to the database
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

"""
# -----------------------------
# 1) Show table schema
# -----------------------------
cur.execute(f"PRAGMA table_info({DB_NAMESPACE})")
columns = cur.fetchall()
print(f"\n📋 Schema of table '{DB_NAMESPACE}':")
for col in columns:
    # col = (cid, name, type, notnull, dflt_value, pk)
    print(f"  Column: {col[1]}, Type: {col[2]}, Not Null: {col[3]}, Default: {col[4]}, PK: {col[5]}")
"""

# -----------------------------
# 2) Show all table rows
# -----------------------------
cur.execute(f"SELECT * FROM {DB_NAMESPACE}")
rows = cur.fetchall()

# Extract column names from cursor description
col_names = [desc[0] for desc in cur.description]

print(f"\n📄 Records in table '{DB_NAMESPACE}':")
if not rows:
    print("  No rows found.")
else:
    # Print header
    print("  | " + " | ".join(col_names) + " |")
    print("  " + "-" * (len(col_names) * 15))
    # Print each row
    for row in rows:
        print("  | " + " | ".join(str(item) for item in row) + " |")

# Close connection
conn.close()

import sqlite3
from config import DB_PATH

TABLE_NAME = "decisions"

# Connect to DB
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

"""
# -----------------------------
# 1) Show schema
# -----------------------------
cur.execute(f"PRAGMA table_info({TABLE_NAME})")
columns = cur.fetchall()
print(f"\n📋 Schema of table '{TABLE_NAME}':")
for col in columns:
    print(f"  Column: {col[1]}, Type: {col[2]}, Not Null: {col[3]}, Default: {col[4]}, PK: {col[5]}")
"""

# -----------------------------
# 2) Show table rows
# -----------------------------
cur.execute(f"SELECT * FROM {TABLE_NAME}")
rows = cur.fetchall()
col_names = [desc[0] for desc in cur.description]

if not rows:
    print(f"\n📄 No records found in table '{TABLE_NAME}'.")
else:
    print(f"\n📄 Records in table '{TABLE_NAME}':")

    # Determine column widths
    col_widths = [len(name) for name in col_names]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val)))

    # Function to format a row
    def format_row(row):
        return "| " + " | ".join(str(val).ljust(col_widths[i]) for i, val in enumerate(row)) + " |"

    # Print header
    print(format_row(col_names))
    print("|-" + "-|-".join("-" * w for w in col_widths) + "-|")

    # Print rows
    for row in rows:
        print(format_row(row))

# Close connection
conn.close()

