import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN")
daily_spending = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Halo! Kirim pengeluaranmu atau foto struk nanti, aku bantu catat!")

async def log_spending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Parsing angka dari teks
    words = text.split()
    amount = next((int(word) for word in words if word.isdigit()), None)

    if amount:
        daily_spending.setdefault(user_id, []).append(amount)
        await update.message.reply_text(f"✅ Tercatat: Rp {amount:,}")
    else:
        await update.message.reply_text("Harap masukkan nominal pengeluaran yang jelas.")

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    total = sum(daily_spending.get(user_id, []))
    await update.message.reply_text(f"💸 Total pengeluaran hari ini: Rp {total:,}")

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("today", today))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_spending))

app.run_polling()