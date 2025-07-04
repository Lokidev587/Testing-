import os
import logging
import re
import cv2
import numpy as np
from io import BytesIO
from PIL import Image
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram import Update, ParseMode
from telegram.ext.callbackcontext import CallbackContext
from nudenet import NudeDetector

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', '8122582244'))

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable not set!")

# NudeDetector
nude_detector = NudeDetector()

class HealthCheckServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running and protecting your group!')

def run_health_server():
    server = HTTPServer(('0.0.0.0', 8080), HealthCheckServer)
    logger.info("Health check server running on port 8080")
    server.serve_forever()

class GroupSecurityBot:
    def __init__(self):
        self.updater = Updater(TELEGRAM_TOKEN, use_context=True)
        self.dp = self.updater.dispatcher
        self.authorized_users = [OWNER_ID]
        self.whitelisted_domains = ['telegram.org', 'wikipedia.org', 'github.com']
        self.promo_keywords = ['buy now', 'discount', 'promo', 'shop now']
        self.drug_keywords = ['weed', 'cocaine', 'heroin', 'drugs']
        self.weapon_keywords = ['gun', 'rifle', 'ammo', 'firearm']
        self.setup_handlers()
        logger.info("Bot initialized successfully")

    def setup_handlers(self):
        self.dp.add_handler(CommandHandler("start", self.send_welcome))
        self.dp.add_handler(CommandHandler("authorize", self.authorize_user))
        self.dp.add_handler(CommandHandler("unauthorize", self.unauthorize_user))
        self.dp.add_handler(MessageHandler(Filters.text | Filters.caption, self.handle_text, run_async=True), group=-100)
        self.dp.add_handler(MessageHandler(Filters.photo | Filters.document, self.handle_media, run_async=True), group=-100)
        self.dp.add_handler(MessageHandler(Filters.sticker | Filters.animation, self.handle_stickers, run_async=True), group=-100)
        self.dp.add_error_handler(self.error_handler)

    def send_welcome(self, update: Update, context: CallbackContext):
        welcome_text = (
            "\U0001F6E1\uFE0F <b>Group Protection Bot</b>\n\n"
            "Add me to your group as admin with these permissions:\n"
            "- Delete messages\n- Ban users\n\n"
            "I will automatically:\n"
            "- Delete unauthorized links\n"
            "- Remove porn, drugs, or weapon content\n"
            "- Ban violators\n\n"
            "<b>Owner commands:</b>\n"
            "/authorize &lt;user_id&gt;\n/unauthorize &lt;user_id&gt;"
        )
        update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)

    def authorize_user(self, update: Update, context: CallbackContext):
        if update.effective_user.id != OWNER_ID:
            update.message.reply_text("❌ Only owner can authorize users.")
            return
        try:
            user_id = int(context.args[0])
            if user_id not in self.authorized_users:
                self.authorized_users.append(user_id)
                update.message.reply_text(f"✅ User {user_id} authorized.")
            else:
                update.message.reply_text("ℹ️ Already authorized.")
        except:
            update.message.reply_text("⚠️ Usage: /authorize <user_id>")

    def unauthorize_user(self, update: Update, context: CallbackContext):
        if update.effective_user.id != OWNER_ID:
            update.message.reply_text("❌ Only owner can unauthorize users.")
            return
        try:
            user_id = int(context.args[0])
            if user_id in self.authorized_users:
                self.authorized_users.remove(user_id)
                update.message.reply_text(f"✅ User {user_id} unauthorized.")
            else:
                update.message.reply_text("ℹ️ Wasn't authorized.")
        except:
            update.message.reply_text("⚠️ Usage: /unauthorize <user_id>")

    def handle_text(self, update: Update, context: CallbackContext):
        message = update.message.text or update.message.caption or ""
        user = update.effective_user
        if self.contains_links(message) and user.id not in self.authorized_users:
            update.message.delete()
            self.warn_user(update, context, "🔗 Links require authorization!")
        elif self.contains_bad_content(message):
            update.message.delete()
            self.ban_user(update, context, "🚫 Banned for policy violation")

    def handle_media(self, update: Update, context: CallbackContext):
        user = update.effective_user
        if user.id in self.authorized_users:
            return
        try:
            file = None
            if update.message.photo:
                file = update.message.photo[-1].get_file()
            elif update.message.document:
                file = update.message.document.get_file()
            if not file:
                return
            file_bytes = BytesIO()
            file.download(out=file_bytes)
            with open('temp_image.jpg', 'wb') as f:
                f.write(file_bytes.getvalue())
            results = nude_detector.detect('temp_image.jpg')
            logger.info(f"NSFW results: {results}")
            for result in results:
                if result['class'] in ['EXPOSED_GENITALIA_F', 'EXPOSED_GENITALIA_M', 'EXPOSED_BREAST_F']:
                    update.message.delete()
                    self.ban_user(update, context, "🔞 NSFW Content")
                    return
            os.remove("temp_image.jpg")
        except Exception as e:
            logger.error(f"Media error: {e}")

    def handle_stickers(self, update: Update, context: CallbackContext):
        user = update.effective_user
        if user.id in self.authorized_users:
            return
        sticker = update.message.sticker
        if not sticker:
            return
        try:
            file = sticker.get_file()
            file_bytes = BytesIO()
            file.download(out=file_bytes)
            with open('temp_sticker.webp', 'wb') as f:
                f.write(file_bytes.getvalue())
            results = nude_detector.detect('temp_sticker.webp')
            logger.info(f"NSFW Sticker results: {results}")
            for result in results:
                if result['class'] in ['EXPOSED_GENITALIA_F', 'EXPOSED_GENITALIA_M', 'EXPOSED_BREAST_F']:
                    update.message.delete()
                    self.ban_user(update, context, "🔞 NSFW Sticker")
                    return
            os.remove("temp_sticker.webp")
        except Exception as e:
            logger.error(f"Sticker error: {e}")

    def contains_links(self, text):
        urls = re.findall(r'https?://\S+', text.lower())
        for url in urls:
            if not any(domain in url for domain in self.whitelisted_domains):
                return True
        return False

    def contains_bad_content(self, text):
        text_lower = text.lower()
        return (
            any(kw in text_lower for kw in self.promo_keywords) or
            any(kw in text_lower for kw in self.drug_keywords) or
            any(kw in text_lower for kw in self.weapon_keywords)
        )

    def warn_user(self, update: Update, context: CallbackContext, message: str):
        try:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"👮 @{update.effective_user.username or 'User'} {message}",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Warning error: {e}")

    def ban_user(self, update: Update, context: CallbackContext, reason: str):
        try:
            context.bot.ban_chat_member(
                chat_id=update.message.chat_id,
                user_id=update.effective_user.id,
                until_date=datetime.now() + timedelta(days=1)
            )
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"🚨 @{update.effective_user.username or 'User'} banned: {reason}",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ban error: {e}")

    def error_handler(self, update: Update, context: CallbackContext):
        logger.error(f'Update "{update}" caused error "{context.error}")

    def run(self):
        server_thread = threading.Thread(target=run_health_server)
        server_thread.daemon = True
        server_thread.start()
        self.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot is now running!")
        self.updater.idle()

if __name__ == '__main__':
    bot = GroupSecurityBot()
    bot.run()
