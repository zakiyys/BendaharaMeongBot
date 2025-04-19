import os
import sqlite3
import requests
from datetime import datetime, timedelta
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters

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

# Utilities

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

# Handlers

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
    import re
    print("[OCR DEBUG] Handler dipanggil.")
    user_id = update.effective_user.id
    photo = update.message.photo
    if not photo:
        print("[OCR DEBUG] Tidak ada foto dalam pesan.")
        return await update.message.reply_text("❌ Tidak ada foto yang ditemukan.")

    file = await photo[-1].get_file()
    print("[OCR DEBUG] File berhasil diambil dari Telegram.")
    path = f"temp_{user_id}.jpg"
    await file.download_to_drive(path)
    print("[OCR DEBUG] File berhasil diunduh ke:", path)

    with open(path, 'rb') as f:
        res = requests.post("https://api.ocr.space/parse/image", files={"filename": f}, data={"apikey": os.getenv("OCR_API_KEY", "helloworld")})
    os.remove(path)

    result_json = res.json()
    print("[OCR DEBUG] Full OCR response:", result_json)

    lines = result_json.get("ParsedResults", [{}])[0].get("ParsedText", "").splitlines()
    print("[OCR DEBUG] Lines:", lines)

    produk = []
    harga = []
    for line in lines:
        line_clean = line.strip()
        if re.fullmatch(r'[\d.,]{4,}', line_clean):
            harga.append(line_clean)
        elif line_clean and len(line_clean) > 3 and not any(keyword in line_clean.lower() for keyword in ["subtotal", "total", "payment", "debit", "thank", "check", "closed"]):
            produk.append(line_clean)

    paired = list(zip(produk, harga))
    print("[OCR DEBUG] Paired:", paired)
    items = []
    for name, val in paired:
        val_clean = val.replace(",", ".")
        try:
            int_val = int(float(val_clean))
            if int_val >= 500:
                items.append((name, int_val))
        except:
            continue

    if not items:
        await update.message.reply_text("❌ Gagal mengenali struk dengan benar. Silakan koreksi manual:\nKetik ulang item seperti:\nNasi Padang 15000\nTeh Botol 6000")
        return

    ocr_cache[user_id] = items
    teks = "\n".join(f"{name[:25]:<25} Rp {amount:,}".replace(",", ".") for name, amount in items)
    await update.message.reply_text(
        f"🧾 Hasil OCR:\n<pre>{teks}</pre>", parse_mode="HTML",
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

# App setup
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