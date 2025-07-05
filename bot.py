import logging
import os
import re
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from nudenet import NudeDetector
import asyncio

# -------------------- CONFIG --------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 123456789))
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

# -------------------- DUMMY HTTP SERVER --------------------
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("✅ Bot is running.".encode('utf-8'))


def start_http_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    logger.info(f"🌐 Dummy HTTP server running on port {port}")
    server.serve_forever()

# -------------------- BOT START --------------------
async def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('authorize', authorize))
    app.add_handler(CommandHandler('unauthorize', unauthorize))
    app.add_handler(MessageHandler(filters.ALL, filter_messages))

    logger.info("🤖 Bot started polling.")
    await app.run_polling()

if __name__ == '__main__':
    # Start the HTTP server in a separate thread
    threading.Thread(target=start_http_server, daemon=True).start()

    # Start the Telegram bot in the main async event loop
    asyncio.run(run_bot())
