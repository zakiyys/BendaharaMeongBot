import os
import json
import base64
import requests
from datetime import datetime
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters
import openai

from db_postgres import (
    insert_spending, get_today, get_week, get_all_entries,
    save_user_timezone, get_user_timezone, delete_last_entry, setup_tables
)

# Locale
with open("locale_texts.json", "r") as f:
    TXT = json.load(f)

# API KEYS
openai.api_key = os.getenv("OPENAI_API_KEY")
VISION_API_KEY = os.getenv("GOOGLE_VISION_API_KEY")
TOKEN = os.getenv("BOT_TOKEN")

KOREKSI = range(1)
ocr_cache = {}

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

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TXT["HELP_MESSAGE"], parse_mode="HTML")

async def set_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Asia/Jakarta", "Asia/Makassar"], ["Asia/Jayapura"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text(TXT["SET_TIMEZONE_PROMPT"], reply_markup=reply_markup)

async def save_timezone_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    zone = update.message.text.strip()
    user_id = update.effective_user.id
    try:
        save_user_timezone(user_id, zone)
        await update.message.reply_text(TXT["TIMEZONE_SAVED"].format(zone=zone), reply_markup=ReplyKeyboardRemove())
    except:
        await update.message.reply_text("❌ Zona waktu tidak dikenali.", reply_markup=ReplyKeyboardRemove())

async def log_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if text.lower() == "hapus":
        delete_last_entry(user_id)
        return await update.message.reply_text(TXT["HAPUS_BERHASIL"])

    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isdigit():
        zone = get_user_timezone(user_id)
        insert_spending(user_id, int(parts[1]), parts[0], zone)
        return await update.message.reply_text(f"✅ {parts[0]} - Rp {int(parts[1]):,}".replace(",", "."))
    await update.message.reply_text(TXT["FORMAT_SALAH"])

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = get_today(user_id)
    user_tz = pytz.timezone(get_user_timezone(user_id))
    if not entries:
        return await update.message.reply_text(TXT["TIDAK_ADA_DATA"])
    total = 0
    lines = []
    for row in entries:
        jam = row['timestamp'].astimezone(user_tz).strftime("%H:%M")
        lines.append(f"{jam} | Rp {row['amount']:,} | {row['description']}".replace(",", "."))
        total += row['amount']
    lines.append(f"Total: Rp {total:,}".replace(",", "."))
    await update.message.reply_text("\n".join(lines))

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = get_week(user_id)
    user_tz = pytz.timezone(get_user_timezone(user_id))
    if not entries:
        return await update.message.reply_text(TXT["TIDAK_ADA_DATA"])
    total = 0
    lines = []
    hari_indo = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    for row in entries:
        d = row['date']
        hari = hari_indo[d.weekday()]
        lines.append(f"{hari}: Rp {row['total']:,}".replace(",", "."))
        total += row['total']
    lines.append(f"Total Minggu Ini: Rp {total:,}".replace(",", "."))
    await update.message.reply_text("\n".join(lines))

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = get_all_entries(user_id)
    if not entries:
        return await update.message.reply_text(TXT["BELUM_ADA_CATATAN"])
    msg = TXT["SUMMARY_HEADER"] + "<pre>"
    for e in entries[:10]:
        msg += f"{e['timestamp'].strftime('%Y-%m-%d')} | Rp {e['amount']:,} | {e['description'][:25]}\n".replace(",", ".")
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
    ocr_cache[user_id] = result
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
    user_id = query.from_user.id
    await query.answer()

    if query.data == "save_ocr":
        if user_id in ocr_cache:
            zone = get_user_timezone(user_id)
            lines = ocr_cache[user_id].splitlines()
            for line in lines:
                if '-' in line:
                    nama, harga = line.split('-', 1)
                    nama = nama.strip()
                    try:
                        harga = int(harga.strip().lower().replace("rp", "").replace(".", ""))
                        insert_spending(user_id, harga, nama, zone)
                    except:
                        continue
            del ocr_cache[user_id]
            await query.edit_message_text("✅ Data dari struk berhasil disimpan.")
        else:
            await query.edit_message_text("⚠️ Tidak ada data untuk disimpan.")
    elif query.data == "edit_ocr":
        await query.edit_message_text(TXT["DISIMPAN_SEMENTARA"])
        return KOREKSI

async def ocr_edit_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    zone = get_user_timezone(user_id)
    for line in update.message.text.strip().splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            insert_spending(user_id, int(parts[1]), parts[0], zone)
    await update.message.reply_text(TXT["KOREKSI_SIMPAN"], reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ============ BOT SETUP ============
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("today", today))
app.add_handler(CommandHandler("week", week))
app.add_handler(CommandHandler("summary", summary))
app.add_handler(CommandHandler("settimezone", set_timezone))
app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^Asia/.*"), save_timezone_choice))
app.add_handler(MessageHandler(filters.PHOTO, ocr_photo))
app.add_handler(CallbackQueryHandler(ocr_decision))
app.add_handler(ConversationHandler(
    entry_points=[CallbackQueryHandler(ocr_decision, pattern="edit_ocr")],
    states={KOREKSI: [MessageHandler(filters.TEXT & ~filters.COMMAND, ocr_edit_manual)]},
    fallbacks=[]
))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_text))

# ============ RUN ============
async def init():
    setup_tables()
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await app.updater.idle()

import asyncio
asyncio.run(init())
