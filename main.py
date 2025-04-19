import os
import json
import pytesseract
import sqlite3
from datetime import datetime, timedelta
from PIL import Image
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

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
    user_id = update.effective_user.id
    photo = await update.message.photo[-1].get_file()
    path = f"temp_{user_id}.jpg"
    await photo.download_to_drive(path)
    text = pytesseract.image_to_string(Image.open(path))
    os.remove(path)
    await update.message.reply_text(f"📸 Teks dari struk:\n{text}")

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
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_spending))
app.add_handler(MessageHandler(filters.PHOTO, ocr_handler))

app.run_polling()