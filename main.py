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
        await update.message.reply_text(f"✅ Tercatat: {text} - Rp {amount:,} ({category})")
    else:
        await update.message.reply_text("Harap masukkan nominal pengeluaran yang jelas.")

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    total = get_total_spending(user_id, days=0)
    await update.message.reply_text(f"💸 Total pengeluaran hari ini: Rp {total:,}")

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    total = get_total_spending(user_id, days=7)
    await update.message.reply_text(f"📅 Total pengeluaran 7 hari terakhir: Rp {total:,}")

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = get_all_entries(user_id)
    if not entries:
        await update.message.reply_text("Belum ada catatan pengeluaran.")
        return

    header = "Tanggal     | Nominal   | Kategori  | Deskripsi\n" + "-"*50 + "\n"
    rows = "\n".join([f"{e[4][:10]} | Rp {e[1]:,} | {e[2]} | {e[3][:20]}" for e in entries[:10]])
    await update.message.reply_text(f"📋 Ringkasan terakhir:\n<pre>{header + rows}</pre>", parse_mode="HTML")

async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = get_all_entries(user_id)
    filename = f"spending_{user_id}.txt"
    with open(filename, "w") as f:
        for e in entries:
            f.write(f"{e[4]} - Rp {e[1]:,} - {e[2]} - {e[3]}\n")
    await update.message.reply_document(InputFile(filename))
    os.remove(filename)

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
        match = re.search(r"(.+?)\s+(\d+[.,]?\d*)$", line)
        if match:
            item = match.group(1).strip()
            price = match.group(2).replace(",", ".")
            try:
                price_int = int(float(price))
                extracted.append((item, price_int))
            except:
                continue

    if extracted:
        ocr_cache[user_id] = extracted
        rows = "\n".join([f"{desc[:25]:<25} Rp {val:,}" for desc, val in extracted])
        keyboard = [[
            InlineKeyboardButton("✔️ Simpan", callback_data="ocr_simpan"),
            InlineKeyboardButton("✏️ Koreksi", callback_data="ocr_koreksi")
        ]]
        await update.message.reply_text(f"🧾 Tabel Pengeluaran:\n<pre>{rows}</pre>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def ocr_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "ocr_simpan":
        data = ocr_cache.get(user_id)
        if not data:
            await query.edit_message_text("❌ Tidak ada data untuk disimpan.")
            return
        for item, val in data:
            insert_spending(user_id, val, item)
        await query.edit_message_text("✅ Semua data berhasil disimpan!")
        ocr_cache.pop(user_id, None)

    elif query.data == "ocr_koreksi":
        await query.edit_message_text("Silakan kirim ulang item-item yang sudah dikoreksi dalam format:\nItem A 15000\nItem B 20000")
        return KOREKSI

async def koreksi_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    new_data = []
    for line in text.splitlines():
        if line.strip():
            parts = line.rsplit(" ", 1)
            if len(parts) == 2 and parts[1].isdigit():
                new_data.append((parts[0], int(parts[1])))

    if new_data:
        for item, val in new_data:
            insert_spending(user_id, val, item)
        await update.message.reply_text("✅ Koreksi disimpan dan dicatat.", reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text("Format tidak dikenali. Ulangi dengan format: NamaItem 15000", reply_markup=ReplyKeyboardRemove())
    ocr_cache.pop(user_id, None)
    return ConversationHandler.END

async def delete_last_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = get_all_entries(user_id)
    if not entries:
        await update.message.reply_text("Tidak ada catatan untuk dihapus.")
        return

    last = entries[0]
    keyboard = [
        [InlineKeyboardButton("Ya, hapus", callback_data=f"hapus:{last[0]}")],
        [InlineKeyboardButton("Batal", callback_data="batal")]
    ]
    await update.message.reply_text(
        f"Ingin menghapus pengeluaran berikut?\n\nRp {last[1]:,} - {last[3]} ({last[2]})",
        reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("hapus:"):
        entry_id = int(query.data.split(":")[1])
        delete_entry(entry_id)
        await query.edit_message_text("✅ Catatan berhasil dihapus.")
    elif query.data == "batal":
        await query.edit_message_text("❎ Dibatalkan.")

# --- BOT SETUP ---
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("today", today))
app.add_handler(CommandHandler("week", week))
app.add_handler(CommandHandler("summary", summary))
app.add_handler(CommandHandler("export", export_data))
app.add_handler(CommandHandler("hapus", delete_last_entry))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(CallbackQueryHandler(ocr_action_handler))
app.add_handler(ConversationHandler(
    entry_points=[CallbackQueryHandler(ocr_action_handler, pattern="ocr_koreksi")],
    states={KOREKSI: [MessageHandler(filters.TEXT & ~filters.COMMAND, koreksi_input)]},
    fallbacks=[]
    per_message=True
))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_spending))
app.add_handler(MessageHandler(filters.PHOTO, ocr_handler))

app.run_polling()
