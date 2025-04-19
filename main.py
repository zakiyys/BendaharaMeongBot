import os
import sqlite3
import requests
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters
import openai

# API key dari environment
openai.api_key = os.getenv("OPENAI_API_KEY")
TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "spending.db"

# Inisialisasi DB
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

# Utility DB

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

# AI Parsing
async def parse_items_with_ai(text):
    prompt = f"""
Berikut ini adalah hasil OCR dari struk belanja:
{text}

Tugasmu adalah mengidentifikasi semua nama item dan harganya. Abaikan bagian seperti subtotal, total, payment, atau teks lain yang bukan item belanja. Berikan hasilnya dalam format:
Nama Item - Harga (angka saja, tanpa Rp)
Contoh:
Nasi Goreng - 15000
Es Teh Manis - 6000
"""
    try:
        print("[AI REQUEST PROMPT]\n" + prompt)
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Kamu adalah asisten yang pandai membaca struk dan mengekstrak item belanja."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        reply = response['choices'][0]['message']['content']
        usage = response.get("usage", {})
        print("[AI RESPONSE RAW]\n" + reply)
        print(f"[AI TOKEN USAGE] prompt: {usage.get('prompt_tokens')}, completion: {usage.get('completion_tokens')}, total: {usage.get('total_tokens')}")

        items = []
        for line in reply.strip().splitlines():
            if '-' in line:
                parts = line.split('-')
                name = parts[0].strip()
                try:
                    price = int(parts[1].strip().replace('.', '').replace(',', ''))
                    if price >= 500:
                        items.append((name, price))
                except:
                    continue
        return items
    except Exception as e:
        print("[AI PARSE ERROR]", str(e))
        return []

# Telegram Handlers
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

    with open(path, 'rb') as f:
        res = requests.post("https://api.ocr.space/parse/image", files={"filename": f}, data={"apikey": os.getenv("OCR_API_KEY", "helloworld")})
    os.remove(path)

    result_json = res.json()
    lines = result_json.get("ParsedResults", [{}])[0].get("ParsedText", "").splitlines()
    full_text = "\n".join(lines)
    print("[OCR DEBUG] Lines:", lines)

    items = await parse_items_with_ai(full_text)

    if not items:
        return await update.message.reply_text("❌ Gagal mengenali struk. Kirim ulang atau koreksi manual:\nContoh: Nasi Goreng 15000")

    ocr_cache[user_id] = items
    teks = "\n".join(f"{name[:25]:<25} Rp {amount:,}".replace(",", ".") for name, amount in items)
    await update.message.reply_text(
        f"🧾 Hasil OCR (AI):\n<pre>{teks}</pre>", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✔️ Simpan", callback_data="save_ocr"),
            InlineKeyboardButton("✏️ Koreksi", callback_data="edit_ocr")
        ]])
    )

async def ocr_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "save_ocr":
        for item in ocr_cache.get(user_id, []):
            insert_spending(user_id, item[1], item[0])
        ocr_cache.pop(user_id, None)
        await query.edit_message_text("✅ Data berhasil disimpan.")
    elif query.data == "edit_ocr":
        await query.edit_message_text("Kirim ulang daftar yang dikoreksi, format: NamaItem 15000\nNamaItem2 12000")
        return KOREKSI

async def ocr_edit_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lines = update.message.text.strip().splitlines()
    for line in lines:
        parts = line.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            insert_spending(user_id, int(parts[1]), parts[0])
    await update.message.reply_text("✅ Koreksi disimpan.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# Setup bot
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
