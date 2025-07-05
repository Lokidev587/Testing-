import logging
import re
import os
import json
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from nudenet import NudeDetector

# -------------------- CONFIG --------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 123456789))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # Example: https://your-app.onrender.com/webhook
AUTH_FILE = 'authorized.json'
SPAM_WORDS = ["spam", "badword1", "offensiveword"]

# -------------------- LOGGING --------------------
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
logger.info("Loading NudeDetector...")
detector = NudeDetector()
logger.info("✅ NudeDetector ready.")

def is_nsfw(file_path):
    try:
        results = detector.detect(file_path)
        return any(d.get("score", 0) > 0.6 for d in results)
    except Exception as e:
        logger.error(f"NSFW detection error: {e}")
        return False

# -------------------- COMMANDS --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 NSFW, Link & Spam Filter Bot is running!")

async def authorize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 You're not authorized.")
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
        await update.message.reply_text("🚫 You're not authorized.")
        return
    if context.args:
        username = context.args[0].lstrip('@')
        authorized_users.discard(username)
        save_authorized(authorized_users)
        await update.message.reply_text(f"❌ Unauthorized @{username}")
    else:
        await update.message.reply_text("Usage: /unauthorize @username")

# -------------------- MESSAGE FILTER --------------------
async def filter_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    sender_username = message.from_user.username or ''

    # Spam word filter
    if message.text and any(word in message.text.lower() for word in SPAM_WORDS):
        await message.delete()
        return

    # Link filter
    if message.text and re.search(r'https?://|www\.|\S+\.\S+', message.text):
        if sender_username not in authorized_users:
            await message.delete()
        return

    # NSFW Media filter
    media = message.photo or message.document or message.video or message.animation
    if media:
        try:
            file = await media[-1].get_file() if isinstance(media, list) else await media.get_file()
            file_path = await file.download_to_drive()
            if is_nsfw(file_path):
                await message.delete()
            os.remove(file_path)
        except Exception as e:
            logger.error(f"Error checking media: {e}")

    # Delete stickers
    if message.sticker:
        await message.delete()

# -------------------- FLASK SERVER --------------------
app = Flask(__name__)
telegram_app = None  # Will hold our bot app instance

@app.route('/')
def home():
    return '✅ Bot is running.', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    if telegram_app:
        return telegram_app.webhook_handler(request)
    return "Bot not initialized", 500

# -------------------- MAIN START --------------------
async def main():
    global telegram_app
    telegram_app = await ApplicationBuilder().token(BOT_TOKEN).build()

    telegram_app.add_handler(CommandHandler('start', start))
    telegram_app.add_handler(CommandHandler('authorize', authorize))
    telegram_app.add_handler(CommandHandler('unauthorize', unauthorize))
    telegram_app.add_handler(MessageHandler(filters.ALL, filter_messages))

    logger.info("Setting webhook...")
    await telegram_app.bot.set_webhook(WEBHOOK_URL)

    # Run Flask app (in same process)
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
