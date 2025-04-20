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

print("✅ ENV PGHOST:", os.getenv("PGHOST"))
print("✅ ENV PGDATABASE:", os.getenv("PGDATABASE"))

KOREKSI = range(1)
ocr_cache = {}

# ============ DEBUG UTILS ============
def debug_print(msg):
    print(f"[DEBUG] {msg}")

# ============ BASIC HANDLERS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    debug_print("/start dipanggil")
    await update.message.reply_text(TXT.get("START_MESSAGE", "Halo! Aku siap mencatat pengeluaranmu."))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    debug_print("/help dipanggil")
    await update.message.reply_text(TXT.get("HELP_MESSAGE", "Ini adalah daftar bantuan..."), parse_mode="HTML")

async def test_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    debug_print("/ping dipanggil")
    await update.message.reply_text("Bot aktif ✅")

# ============ BOT SETUP ============
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("ping", test_ping))

# ============ RUNNING ============
async def init():
    debug_print("Menjalankan setup_tables()...")
    setup_tables()
    debug_print("Setup table selesai.")

    await app.initialize()
    debug_print("App initialized.")

    await app.start()
    debug_print("App started.")

    await app.updater.start_polling()
    debug_print("Polling dimulai!")

    await app.updater.idle()

import asyncio
asyncio.run(init())
