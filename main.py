import os
import sqlite3
import base64
import requests
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")
VISION_API_KEY = os.getenv("GOOGLE_VISION_API_KEY")
TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "spending.db"

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

ocr_cache = {}
KOREKSI = range(1)

def insert_spending(user_id, amount, description):
    now = datetime.now().isoformat()
    c.execute("INSERT INTO spending (user_id, amount, description, timestamp) VALUES (?, ?, ?, ?)", (user_id, amount, description, now))
    conn.commit()

def get_total(user_id, days=0):
    if days == 0:
        c.execute("SELECT SUM(amount) FROM spending WHERE user_id = ? AND date(timestamp) = date('now')", (user_id,))
    else:
        c.execute("SELECT SUM(amount) FROM spending WHERE user_id = ? AND timestamp >= ?", (user_id, (datetime.now() - timedelta(days=days)).isoformat()))
    total = c.fetchone()[0]
    return total if total else 0

def get_all_entries(user_id):
    c.execute("SELECT amount, description, timestamp FROM spending WHERE user_id = ? ORDER BY timestamp DESC", (user_id,))
    return c.fetchall()

async def google_vision_ocr(image_path):
    with open(image_path, "rb") as image_file:
        content = base64.b64encode(image_file.read()).decode("utf-8")

    url = f"https://vision.googleapis.com/v1/images:annotate?key={VISION_API_KEY}"
    headers = {"Content-Type": "application/json"}
    body = {
        "requests": [
            {
                "image": {"content": content},
                "features": [{"type": "TEXT_DETECTION"}]
            }
        ]
    }

    res = requests.post(url, json=body, headers=headers)
    res_json = res.json()
    try:
        text = res_json['responses'][0]['fullTextAnnotation']['text']
        return text
    except:
        print("[VISION ERROR]", res_json)
        return ""

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
   - Anda Hemat (jika ada)
   - Tunai dibayar
   - Kembalian

Format tampilan harus rapi dan semua harga diformat dengan titik ribuan. Contoh:
Nasi Goreng (2X) - Rp 24.000
Es Teh (1X) - Rp 6.000
...

<b>Total Belanja:</b> Rp 75.400
<b>Diskon:</b> Rp 2.000
<b>PPN:</b> Rp 7.036
<b>Tunai:</b> Rp 100.500
<b>Kembali:</b> Rp 25.100
"""

    try:
        print("[AI REQUEST PROMPT]\n" + prompt)
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Kamu adalah asisten yang pandai membaca struk dan mengekstrak item belanja."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            user="bendahara_bot"
        )
        reply = response['choices'][0]['message']['content']
        usage = response.get("usage", {})
        print("[AI RESPONSE RAW]\n" + reply)
        print(f"[AI TOKEN USAGE] prompt: {usage.get('prompt_tokens')}, completion: {usage.get('completion_tokens')}, total: {usage.get('total_tokens')}")
        return reply
    except Exception as e:
        print("[AI PARSE ERROR]", str(e))
        return ""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Halo! Kirim pengeluaran seperti 'Nasi Goreng 15000' atau upload foto struk belanja.")

async def log_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if not text:
        return
    if text.lower().startswith("hapus"):
        return await delete_last(update, context)
    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isdigit():
        insert_spending(user_id, int(parts[1]), parts[0])
        await update.message.reply_text(f"✅ {parts[0]} - Rp {int(parts[1]):,}".replace(",", "."))
    else:
        await update.message.reply_text("Format salah. Contoh: Es Teh 5000")

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = get_total(update.effective_user.id)
    await update.message.reply_text(f"Hari ini: Rp {total:,}".replace(",", "."))

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = get_total(update.effective_user.id, 7)
    await update.message.reply_text(f"7 Hari terakhir: Rp {total:,}".replace(",", "."))

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = get_all_entries(user_id)
    if not entries:
        await update.message.reply_text("Belum ada catatan.")
        return
    msg = "📋 Ringkasan:\n<pre>"
    for e in entries[:10]:
        msg += f"{e[2][:10]} | Rp {e[0]:,} | {e[1][:25]}\n".replace(",", ".")
    msg += "</pre>"
    await update.message.reply_text(msg, parse_mode="HTML")

async def delete_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    c.execute("SELECT id FROM spending WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1", (user_id,))
    row = c.fetchone()
    if row:
        c.execute("DELETE FROM spending WHERE id = ?", (row[0],))
        conn.commit()
        await update.message.reply_text("✅ Entri terakhir dihapus.")
    else:
        await update.message.reply_text("Tidak ada data.")

async def ocr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photo = update.message.photo
    if not photo:
        return await update.message.reply_text("❌ Tidak ada foto yang ditemukan.")

    file = await photo[-1].get_file()
    path = f"temp_{user_id}.jpg"
    await file.download_to_drive(path)

    raw_text = await google_vision_ocr(path)
    print("[OCR DEBUG TEXT]\n" + raw_text)

    reply = await parse_items_with_ai(raw_text)
    if not reply:
        return await update.message.reply_text("❌ Gagal mengenali struk. Kirim ulang atau koreksi manual.")

    await update.message.reply_text(
        f"🧾 Hasil OCR (Google + AI):\n<pre>{reply}</pre>", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✔️ Simpan", callback_data="save_ocr"),
            InlineKeyboardButton("✏️ Koreksi", callback_data="edit_ocr")
        ]])
    )

async def ocr_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    await query.edit_message_text("✅ Data disimpan sementara. Koreksi manual belum didukung penuh.")

async def ocr_edit_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lines = update.message.text.strip().splitlines()
    for line in lines:
        parts = line.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            insert_spending(user_id, int(parts[1]), parts[0])
    await update.message.reply_text("✅ Koreksi disimpan.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

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