import logging
import re
import os
import json
import threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from nsfw_detector import predict

# -------------------- CONFIG --------------------
BOT_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN'  # Replace this
OWNER_ID = 123456789  # Replace with your numeric Telegram user ID
AUTH_FILE = 'authorized.json'
SPAM_WORDS = ["spam", "badword1", "offensiveword", "test"]  # Add spam words here
# ------------------------------------------------

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------- AUTHORIZED USERS --------------------
def load_authorized():
    if os.path.exists(AUTH_FILE):
        with open(AUTH_FILE, 'r') as f:
            return set(json.load(f))
    return set()

def save_authorized(users):
    with open(AUTH_FILE, 'w') as f:
        json.dump(list(users), f)

authorized_users = load_authorized()

# -------------------- NSFW DETECTOR --------------------
logger.info("Loading NSFW detector model. This may take a few seconds...")
model = predict.load_model()  # Downloads the model on first run
logger.info("NSFW detector loaded successfully.")

# -------------------- COMMANDS --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 NSFW, Link & Spam Filter Bot is running!")

async def authorize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return
    if context.args:
        username = context.args[0].lstrip('@')
        authorized_users.add(username)
        save_authorized(authorized_users)
        await update.message.reply_text(f"✅ Authorized @{username}")
    else:
        await update.message.reply_text("Usage: /authorize @username")

async def unauthorize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return
    if context.args:
        username = context.args[0].lstrip('@')
        authorized_users.discard(username)
        save_authorized(authorized_users)
        await update.message.reply_text(f"❌ Unauthorized @{username}")
    else:
        await update.message.reply_text("Usage: /unauthorize @username")

# -------------------- MESSAGE HANDLER --------------------
async def filter_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    sender_username = message.from_user.username or ''

    # --- Spam Word Filter ---
    if message.text and any(word.lower() in message.text.lower() for word in SPAM_WORDS):
        try:
            await message.delete()
            logger.info(f"Deleted spam word message from @{sender_username}")
        except Exception as e:
            logger.error(f"Error deleting spam message: {e}")
        return

    # --- Link Filter ---
    if message.text and re.search(r'https?://|www\.|\S+\.\S+', message.text):
        if sender_username not in authorized_users:
            try:
                await message.delete()
                logger.info(f"Deleted link from @{sender_username}")
            except Exception as e:
                logger.error(f"Error deleting link: {e}")
        return

    # --- NSFW Filter for Media ---
    media = message.photo or message.document or message.video or message.animation
    if media:
        try:
            file = await media[-1].get_file() if isinstance(media, list) else await media.get_file()
            file_path = await file.download_to_drive()

            results = predict.classify(model, file_path)
            os.remove(file_path)

            prediction = results.get(file_path, {})
            # NSFW threshold: 60%
            if prediction.get("porn", 0) > 0.6 or prediction.get("sexy", 0) > 0.6:
                await message.delete()
                logger.info(f"Deleted NSFW media from @{sender_username}")
        except Exception as e:
            logger.error(f"NSFW detection error: {e}")

    # --- Delete Stickers (webp, tgs, etc.) ---
    if message.sticker:
        try:
            await message.delete()
            logger.info(f"Deleted sticker from @{sender_username}")
        except Exception as e:
            logger.error(f"Error deleting sticker: {e}")

# -------------------- DUMMY FLASK SERVER --------------------
app = Flask(__name__)

@app.route('/')
def home():
    return '✅ Bot is alive.', 200

def run_server():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

# -------------------- BOT MAIN --------------------
async def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('authorize', authorize))
    app.add_handler(CommandHandler('unauthorize', unauthorize))
    app.add_handler(MessageHandler(filters.ALL, filter_messages))

    logger.info("🤖 Bot started and polling for updates.")
    await app.run_polling()

# -------------------- ENTRY POINT --------------------
if __name__ == '__main__':
    threading.Thread(target=run_server).start()

    import asyncio
    asyncio.run(run_bot())
