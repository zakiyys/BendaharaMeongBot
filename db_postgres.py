import os
import pg8000.native
from datetime import datetime, timedelta

DB_CONFIG = {
    "user": os.getenv("PGUSER"),
    "password": os.getenv("PGPASSWORD"),
    "host": os.getenv("PGHOST"),
    "port": int(os.getenv("PGPORT", 5432)),
    "database": os.getenv("PGDATABASE"),
}

# ========== CONNECTION ==========
def get_conn():
    return pg8000.native.Connection(**DB_CONFIG)

# ========== SETUP ==========
def setup_tables():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS spending (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            amount INT,
            description TEXT,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            timezone TEXT DEFAULT 'Asia/Jakarta'
        );
    """)
    conn.commit()
    cursor.close()
    conn.close()

# ========== DATA OPS ==========
def insert_spending(user_id, amount, desc, zone):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO spending (user_id, amount, description, timestamp) VALUES (%s, %s, %s, %s)",
        (user_id, amount, desc, datetime.now())
    )
    conn.commit()
    cursor.close()
    conn.close()

def get_today(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT amount, description, timestamp FROM spending
        WHERE user_id = %s AND timestamp::date = CURRENT_DATE
        ORDER BY timestamp ASC
    """, (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [
        {"amount": r[0], "description": r[1], "timestamp": r[2]} for r in rows
    ]

def get_week(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DATE(timestamp), SUM(amount) FROM spending
        WHERE user_id = %s AND timestamp >= CURRENT_DATE - INTERVAL '6 day'
        GROUP BY DATE(timestamp)
        ORDER BY DATE(timestamp) ASC
    """, (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [
        {"date": r[0], "total": r[1]} for r in rows
    ]

def get_all_entries(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT amount, description, timestamp FROM spending
        WHERE user_id = %s ORDER BY timestamp DESC
    """, (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [
        {"amount": r[0], "description": r[1], "timestamp": r[2]} for r in rows
    ]

def delete_last_entry(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM spending
        WHERE id = (
            SELECT id FROM spending WHERE user_id = %s
            ORDER BY timestamp DESC LIMIT 1
        )
    """, (user_id,))
    conn.commit()
    cursor.close()
    conn.close()

# ========== TIMEZONE OPS ==========
def save_user_timezone(user_id, zone):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, timezone)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET timezone = EXCLUDED.timezone
    """, (user_id, zone))
    conn.commit()
    cursor.close()
    conn.close()

def get_user_timezone(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT timezone FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else "Asia/Jakarta"
