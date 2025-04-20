import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

print("🐍 Main.py dimulai!")

TOKEN = os.getenv("BOT_TOKEN")
app = ApplicationBuilder().token(TOKEN).build()

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot aktif ✅")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Kamu berkata: {update.message.text}")

app.add_handler(CommandHandler("ping", ping))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

async def init():
    print("[DEBUG] Inisialisasi bot...")
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.initialize()
    await app.start()
    print("[DEBUG] Bot jalan, polling dimulai...")
    await app.updater.start_polling()
    await app.updater.idle()

asyncio.run(init())
