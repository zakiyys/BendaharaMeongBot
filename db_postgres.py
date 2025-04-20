import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
import pytz

from dotenv import load_dotenv
load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

print("[DEBUG] DATABASE_URL =", DB_URL)

# ========== SETUP TABLE ==========
cursor.execute("""
CREATE TABLE IF NOT EXISTS spending (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    amount INTEGER,
    description TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id BIGINT PRIMARY KEY,
    timezone TEXT
);
""")
conn.commit()

# ========== DB FUNCTIONS ==========
def insert_spending(user_id, amount, description, user_tz):
    now = datetime.now(pytz.timezone(user_tz))
    cursor.execute("""
        INSERT INTO spending (user_id, amount, description, timestamp)
        VALUES (%s, %s, %s, %s)
    """, (user_id, amount, description, now))
    conn.commit()

def get_user_timezone(user_id):
    cursor.execute("SELECT timezone FROM user_settings WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    return row['timezone'] if row else 'Asia/Jakarta'

def save_user_timezone(user_id, zone):
    cursor.execute("""
        INSERT INTO user_settings (user_id, timezone)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET timezone = EXCLUDED.timezone
    """, (user_id, zone))
    conn.commit()

def get_today(user_id):
    cursor.execute("""
        SELECT description, amount, timestamp FROM spending
        WHERE user_id = %s AND timestamp >= (CURRENT_DATE AT TIME ZONE 'UTC')
        ORDER BY timestamp
    """, (user_id,))
    return cursor.fetchall()

def get_week(user_id):
    cursor.execute("""
        SELECT DATE(timestamp) as date, SUM(amount) as total
        FROM spending
        WHERE user_id = %s AND timestamp >= NOW() - INTERVAL '7 days'
        GROUP BY date
        ORDER BY date
    """, (user_id,))
    return cursor.fetchall()

def get_all_entries(user_id):
    cursor.execute("""
        SELECT amount, description, timestamp FROM spending
        WHERE user_id = %s ORDER BY timestamp DESC
    """, (user_id,))
    return cursor.fetchall()

def delete_last_entry(user_id):
    cursor.execute("""
        DELETE FROM spending
        WHERE id = (
            SELECT id FROM spending
            WHERE user_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
        )
    """, (user_id,))
    conn.commit()