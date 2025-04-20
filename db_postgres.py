import os
import pg8000.native
import urllib.parse
from datetime import datetime, timedelta
import pytz

# Manual parse DATABASE_URL
db_url = os.getenv("DATABASE_URL")
parsed = urllib.parse.urlparse(db_url)

conn = pg8000.native.Connection(
    user=parsed.username,
    password=parsed.password,
    host=parsed.hostname,
    port=parsed.port,
    database=parsed.path.lstrip("/")
)
# ========== SETUP TABLE ==========
conn.run("""
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

# ========== DB FUNCTIONS ==========
def insert_spending(user_id, amount, description, user_tz):
    now = datetime.now(pytz.timezone(user_tz))
    conn.run("""
        INSERT INTO spending (user_id, amount, description, timestamp)
        VALUES (:user_id, :amount, :description, :timestamp)
    """, user_id=user_id, amount=amount, description=description, timestamp=now)


def get_user_timezone(user_id):
    res = conn.run("SELECT timezone FROM user_settings WHERE user_id = :user_id", user_id=user_id)
    return res[0]["timezone"] if res else "Asia/Jakarta"


def save_user_timezone(user_id, zone):
    conn.run("""
        INSERT INTO user_settings (user_id, timezone)
        VALUES (:user_id, :zone)
        ON CONFLICT (user_id) DO UPDATE SET timezone = EXCLUDED.timezone
    """, user_id=user_id, zone=zone)


def get_today(user_id):
    return conn.run("""
        SELECT description, amount, timestamp FROM spending
        WHERE user_id = :user_id AND timestamp >= (CURRENT_DATE AT TIME ZONE 'UTC')
        ORDER BY timestamp
    """, user_id=user_id)


def get_week(user_id):
    return conn.run("""
        SELECT DATE(timestamp) as date, SUM(amount) as total
        FROM spending
        WHERE user_id = :user_id AND timestamp >= NOW() - INTERVAL '7 days'
        GROUP BY date
        ORDER BY date
    """, user_id=user_id)


def get_all_entries(user_id):
    return conn.run("""
        SELECT amount, description, timestamp FROM spending
        WHERE user_id = :user_id ORDER BY timestamp DESC
    """, user_id=user_id)


def delete_last_entry(user_id):
    conn.run("""
        DELETE FROM spending
        WHERE id = (
            SELECT id FROM spending
            WHERE user_id = :user_id
            ORDER BY timestamp DESC
            LIMIT 1
        )
    """, user_id=user_id)
