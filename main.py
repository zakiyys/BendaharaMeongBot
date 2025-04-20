import os
import sqlite3
import base64
import json
import requests
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters
import openai

# Load locale
with open("locale_texts.json", "r") as f:
    TXT = json.load(f)

# API KEYS
openai.api_key = os.getenv("OPENAI_API_KEY")
VISION_API_KEY = os.getenv("GOOGLE_VISION_API_KEY")
TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "spending.db"

# DB SETUP
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS spending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    description TEXT,
    timestamp TEXT
)''')
conn.commit()

KOREKSI = range(1)

# ============ UTILITY ============
def insert_spending(user_id, amount, description):
    now = datetime.now().isoformat()
    c.execute("INSERT INTO spending (user_id, amount, description, timestamp) VALUES (?, ?, ?, ?)", (user_id, amount, description, now))
    conn.commit()

def get_today(user_id):
    c.execute("SELECT description, amount, timestamp FROM spending WHERE user_id = ? AND date(timestamp) = date('now') ORDER BY timestamp", (user_id,))
    return c.fetchall()

def get_week(user_id):
    c.execute("SELECT date(timestamp), SUM(amount) FROM spending WHERE user_id = ? AND timestamp >= ? GROUP BY date(timestamp) ORDER BY date(timestamp)", (user_id, (datetime.now() - timedelta(days=7)).isoformat()))
    return c.fetchall()

def get_all_entries(user_id):
    c.execute("SELECT amount, description, timestamp FROM spending WHERE user_id = ? ORDER BY timestamp DESC", (user_id,))
    return c.fetchall()

# ============ OCR GOOGLE ============
async def google_vision_ocr(image_path):
    with open(image_path, "rb") as image_file:
        content = base64.b64encode(image_file.read()).decode("utf-8")

    url = f"https://vision.googleapis.com/v1/images:annotate?key={VISION_API_KEY}"
    headers = {"Content-Type": "application/json"}
    body = {
        "requests": [{"image": {"content": content}, "features": [{"type": "TEXT_DETECTION"}]}]
    }
    res = requests.post(url, json=body, headers=headers)
    return res.json()['responses'][0].get('fullTextAnnotation', {}).get('text', '')

# ============ GPT PARSING ============
async def parse_items_with_ai(text):
    prompt = f"""
Berikut ini adalah hasil OCR dari struk belanja:
{text}

Tugas kamu:
1. Ekstrak semua item belanjaan dengan format:
   Nama Item (Qty X) - TotalHarga
2. Tambahkan bagian di bawahnya:
   - Total Belanja
   - PPN (jika ada)
   - Diskon (jika ada)
   - Tunai dibayar
   - Kembalian
Semua harga diformat titik ribuan.
"""
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "Kamu adalah asisten belanja yang akurat."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2,
        user="bendahara_bot"
    )
    return response['choices'][0]['message']['content']

# ============ HANDLERS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TXT["START_MESSAGE"])

async def log_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if text.lower() == "hapus":
        c.execute("DELETE FROM spending WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1", (user_id,))
        conn.commit()
        return await update.message.reply_text(TXT["HAPUS_BERHASIL"])

    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isdigit():
        insert_spending(user_id, int(parts[1]), parts[0])
        return await update.message.reply_text(f"✅ {parts[0]} - Rp {int(parts[1]):,}".replace(",", "."))
    await update.message.reply_text(TXT["FORMAT_SALAH"])

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = get_today(user_id)
    if not entries:
        return await update.message.reply_text(TXT["TIDAK_ADA_DATA"])
    total = 0
    lines = []
    for desc, amt, ts in entries:
        jam = ts[11:16]
        lines.append(f"{jam} | Rp {amt:,} | {desc}".replace(",", "."))
        total += amt
    lines.append(f"Total: Rp {total:,}".replace(",", "."))
    await update.message.reply_text("\n".join(lines))

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = get_week(user_id)
    if not entries:
        return await update.message.reply_text(TXT["TIDAK_ADA_DATA"])
    total = 0
    lines = []
    for date, amt in entries:
        hari = datetime.strptime(date, "%Y-%m-%d").strftime("%A")
        lines.append(f"{hari}: Rp {amt:,}".replace(",", "."))
        total += amt
    lines.append(f"Total Minggu Ini: Rp {total:,}".replace(",", "."))
    await update.message.reply_text("\n".join(lines))

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = get_all_entries(user_id)
    if not entries:
        return await update.message.reply_text(TXT["BELUM_ADA_CATATAN"])
    msg = TXT["SUMMARY_HEADER"] + "<pre>"
    for e in entries[:10]:
        msg += f"{e[2][:10]} | Rp {e[0]:,} | {e[1][:25]}\n".replace(",", ".")
    msg += "</pre>"
    await update.message.reply_text(msg, parse_mode="HTML")

async def ocr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    file = await update.message.photo[-1].get_file()
    path = f"temp_{user_id}.jpg"
    await file.download_to_drive(path)
    text = await google_vision_ocr(path)
    if not text:
        return await update.message.reply_text(TXT["OCR_ERROR"])
    result = await parse_items_with_ai(text)
    await update.message.reply_text(
        f"\U0001F4BE Hasil OCR (Google + AI):\n<pre>{result}</pre>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(TXT["BUTTON_SIMPAN"], callback_data="save_ocr"),
            InlineKeyboardButton(TXT["BUTTON_KOREKSI"], callback_data="edit_ocr")
        ]])
    )

async def ocr_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(TXT["DISIMPAN_SEMENTARA"])

async def ocr_edit_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    for line in update.message.text.strip().splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            insert_spending(user_id, int(parts[1]), parts[0])
    await update.message.reply_text(TXT["KOREKSI_SIMPAN"], reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ============ BOT SETUP ============
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("today", today))
app.add_handler(CommandHandler("week", week))
app.add_handler(CommandHandler("summary", summary))
app.add_handler(MessageHandler(filters.PHOTO, ocr_photo))
app.add_handler(CallbackQueryHandler(ocr_decision))
app.add_handler(ConversationHandler(
    entry_points=[CallbackQueryHandler(ocr_decision, pattern="edit_ocr")],
    states={KOREKSI: [MessageHandler(filters.TEXT & ~filters.COMMAND, ocr_edit_manual)]},
    fallbacks=[]
))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_text))
app.run_polling()