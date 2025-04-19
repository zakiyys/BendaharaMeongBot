import os
import json
import sqlite3
import requests
from datetime import datetime, timedelta
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler, ConversationHandler

TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "spending.db"

# --- INIT DB ---
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS spending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    category TEXT,
    description TEXT,
    timestamp TEXT
)''')
conn.commit()

ocr_cache = {}
KOREKSI = range(1)

# --- HELPERS ---
def insert_spending(user_id, amount, description="", category="Umum"):
    now = datetime.now().isoformat()
    c.execute("INSERT INTO spending (user_id, amount, category, description, timestamp) VALUES (?, ?, ?, ?, ?)",
              (user_id, amount, category, description, now))
    conn.commit()

def get_total_spending(user_id, days=0):
    if days == 0:
        date_limit = datetime.now().date().isoformat()
        c.execute("SELECT SUM(amount) FROM spending WHERE user_id = ? AND date(timestamp) = ?", (user_id, date_limit))
    else:
        date_limit = (datetime.now() - timedelta(days=days)).isoformat()
        c.execute("SELECT SUM(amount) FROM spending WHERE user_id = ? AND timestamp >= ?", (user_id, date_limit))
    total = c.fetchone()[0]
    return total if total else 0

def get_all_entries(user_id):
    c.execute("SELECT id, amount, category, description, timestamp FROM spending WHERE user_id = ? ORDER BY timestamp DESC", (user_id,))
    return c.fetchall()

def delete_entry(entry_id):
    c.execute("DELETE FROM spending WHERE id = ?", (entry_id,))
    conn.commit()

# --- COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Halo! Kirim pengeluaranmu (contoh: Makan siang 25000 Makanan) atau foto struk nanti, aku bantu catat!")

async def log_spending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    words = text.split()
    amount = next((int(word) for word in words if word.isdigit()), None)
    category = words[-1] if not words[-1].isdigit() else "Umum"

    if amount:
        insert_spending(user_id, amount, text, category)
        await update.message.reply_text(f"✅ Tercatat: {text} - Rp {amount:,}".replace(",", "."))
    else:
        await update.message.reply_text("Harap masukkan nominal pengeluaran yang jelas.")

async def ocr_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import re
    user_id = update.effective_user.id
    photo = await update.message.photo[-1].get_file()
    path = f"temp_{user_id}.jpg"
    await photo.download_to_drive(path)

    api_key = os.getenv("OCR_API_KEY", "helloworld")
    with open(path, 'rb') as f:
        response = requests.post(
            'https://api.ocr.space/parse/image',
            files={'filename': f},
            data={'apikey': api_key, 'language': 'eng'}
        )

    os.remove(path)
    result = response.json()
    if result.get("IsErroredOnProcessing"):
        await update.message.reply_text("❌ Gagal memproses gambar.")
        return

    parsed_text = result["ParsedResults"][0]["ParsedText"]
    lines = [line.strip() for line in parsed_text.splitlines() if line.strip() != '']

    combined = []
    skip_next = False
    for i, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        if i + 1 < len(lines) and re.match(r"^\d+[.,]?\d*$", lines[i+1].replace(",", "").replace(".", "")):
            combined.append(f"{line} {lines[i+1]}")
            skip_next = True
        else:
            combined.append(line)

    extracted = []
    for line in combined:
        match = re.search(r"(.+?)\s+(\d{2,3}(?:[.,]\d{3})+)$", line)
        if match:
            item = match.group(1).strip()
            price = match.group(2).replace(",", ".")
            try:
                price_int = int(float(price))
                if price_int >= 500:  # Minimal harga masuk akal
                    extracted.append((item, price_int))
            except:
                continue

    if extracted:
        ocr_cache[user_id] = extracted
        rows = "\n".join([f"{desc[:25]:<25} Rp {val:,}".replace(",", ".") for desc, val in extracted])
        keyboard = [[
            InlineKeyboardButton("✔️ Simpan", callback_data="ocr_simpan"),
            InlineKeyboardButton("✏️ Koreksi", callback_data="ocr_koreksi")
        ]]
        await update.message.reply_text(f"🧾 Tabel Pengeluaran:\n<pre>{rows}</pre>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
