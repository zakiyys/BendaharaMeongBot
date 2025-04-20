import os
import json
import base64
import requests
import asyncio
from datetime import datetime
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

from db_postgres import (
    insert_spending, get_today, get_week, get_all_entries,
    save_user_timezone, get_user_timezone, delete_last_entry, setup_tables
)

print("🐍 Main.py dimulai!")

# ========== CONFIGURATION ==========
TOKEN = os.getenv("BOT_TOKEN")
VISION_API_KEY = os.getenv("GOOGLE_VISION_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ========== BOT ==========
KOREKSI = range(1)
ocr_cache = {}

app = ApplicationBuilder().token(TOKEN).build()

# ========== HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("[DEBUG] /start dipanggil")
    await update.message.reply_text("Halo! Aku siap bantu catat pengeluaran kamu. Kirim saja pesan seperti: \n\nMie Ayam 15000")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("[DEBUG] /ping dipanggil")
    await update.message.reply_text("Bot aktif ✅")

# ========== REGISTER HANDLER ==========
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("ping", ping))

# ========== RUN ==========
async def init():
    print("[DEBUG] Menjalankan setup_tables()...")
    setup_tables()
    print("[DEBUG] Setup table selesai.")

    await app.bot.delete_webhook(drop_pending_updates=True)

    await app.initialize()
    print("[DEBUG] App initialized.")

    await app.start()
    print("[DEBUG] App started.")

    await app.updater.start_polling()
    print("[DEBUG] Polling dimulai!")

    await app.updater.idle()

asyncio.run(init())
