import os
import logging
import re
import threading
import tempfile
from datetime import datetime, timedelta
from io import BytesIO
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
from PIL import Image
from telegram import ParseMode, Update
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, CallbackContext
)
from nudenet import NudeDetector

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', '8122582244'))  # replace with your owner ID

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable not set!")

# Initialize NudeDetector (will download model automatically)
nude_detector = NudeDetector()

# Dummy HTTP server for health check (Render compatibility)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running and protecting your group!")

def run_health_server():
    server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
    logger.info("Health check server running on port 8080")
    server.serve_forever()

class GroupProtectionBot:
    def __init__(self):
        self.updater = Updater(TELEGRAM_TOKEN, use_context=True)
        self.dp = self.updater.dispatcher

        # Admins authorized to post links
        self.authorized_admins = [OWNER_ID]
        # Whitelisted domains allowed in links
        self.whitelisted_domains = ['telegram.org', 'wikipedia.org', 'github.com']

        # Keywords for filtering in text messages
        self.promo_keywords = ['buy now', 'discount', 'promo', 'shop now']
        self.drug_keywords = ['weed', 'cocaine', 'heroin', 'drugs', 'medicine', 'pharma']
        self.weapon_keywords = ['gun', 'rifle', 'ammo', 'firearm', 'pistol']
        self.terror_keywords = ['isis', 'terrorism', 'bomb', 'explosive']
        self.hentai_keywords = ['hentai', 'adult', 'porn', 'sex', 'xxx']

        # Banned stickers by file_unique_id
        self.banned_stickers = set()

        self.setup_handlers()
        logger.info("Bot initialized")

    def setup_handlers(self):
        # Commands
        self.dp.add_handler(CommandHandler("start", self.cmd_start))
        self.dp.add_handler(CommandHandler("approve_admin", self.cmd_approve_admin))
        self.dp.add_handler(CommandHandler("revoke_admin", self.cmd_revoke_admin))
        self.dp.add_handler(CommandHandler("ban_sticker", self.cmd_ban_sticker))

        # Messages
        self.dp.add_handler(MessageHandler(Filters.text | Filters.caption, self.handle_text))
        self.dp.add_handler(MessageHandler(Filters.photo | Filters.document, self.handle_media))
        self.dp.add_handler(MessageHandler(Filters.sticker, self.handle_sticker))

        # Error logging
        self.dp.add_error_handler(self.error_handler)

    def cmd_start(self, update: Update, context: CallbackContext):
        welcome_text = (
            "🛡️ *Group Protection Bot*\n\n"
            "Add me to your group as admin with permissions to delete messages and ban users.\n\n"
            "I will:\n"
            "- Delete unauthorized links\n"
            "- Remove NSFW, drug, weapon, terrorism, hentai content\n"
            "- Ban offenders for 1 day\n\n"
            "Owner commands:\n"
            "/approve_admin <user_id> - Allow user to post links\n"
            "/revoke_admin <user_id> - Revoke link permissions\n"
            "/ban_sticker - Reply to a sticker to ban it"
        )
        update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

    def cmd_approve_admin(self, update: Update, context: CallbackContext):
        if update.effective_user.id != OWNER_ID:
            update.message.reply_text("❌ Only the owner can approve admins.")
            return
        try:
            user_id = int(context.args[0])
            if user_id not in self.authorized_admins:
                self.authorized_admins.append(user_id)
                update.message.reply_text(f"✅ User {user_id} approved to post links.")
            else:
                update.message.reply_text("ℹ️ User already authorized.")
        except Exception:
            update.message.reply_text("⚠️ Usage: /approve_admin <user_id>")

    def cmd_revoke_admin(self, update: Update, context: CallbackContext):
        if update.effective_user.id != OWNER_ID:
            update.message.reply_text("❌ Only the owner can revoke admins.")
            return
        try:
            user_id = int(context.args[0])
            if user_id in self.authorized_admins:
                self.authorized_admins.remove(user_id)
                update.message.reply_text(f"✅ User {user_id} revoked from link posting.")
            else:
                update.message.reply_text("ℹ️ User wasn't authorized.")
        except Exception:
            update.message.reply_text("⚠️ Usage: /revoke_admin <user_id>")

    def cmd_ban_sticker(self, update: Update, context: CallbackContext):
        if update.effective_user.id != OWNER_ID:
            update.message.reply_text("❌ Only the owner can ban stickers.")
            return
        reply = update.message.reply_to_message
        if not reply or not reply.sticker:
            update.message.reply_text("⚠️ Please reply to a sticker to ban it.")
            return
        sticker = reply.sticker
        self.banned_stickers.add(sticker.file_unique_id)
        update.message.reply_text(f"✅ Sticker banned (ID: {sticker.file_unique_id})")

    def handle_text(self, update: Update, context: CallbackContext):
        if not update.message:
            return
        text = update.message.text or update.message.caption or ""
        user = update.effective_user

        # Check for links in text
        if self.contains_links(text) and user.id not in self.authorized_admins and user.id != OWNER_ID:
            try:
                update.message.delete()
                self.warn_user(update, context, "🔗 Links require owner/admin approval!")
            except Exception as e:
                logger.error(f"Failed to delete message with unauthorized link: {e}")
            return

        # Check for forbidden keywords in text
        lower_text = text.lower()
        if any(k in lower_text for k in self.promo_keywords + self.drug_keywords + self.weapon_keywords + self.terror_keywords + self.hentai_keywords):
            try:
                update.message.delete()
                self.ban_user(update, context, "🚫 Banned for forbidden content")
            except Exception as e:
                logger.error(f"Failed to delete banned content message: {e}")

    def handle_media(self, update: Update, context: CallbackContext):
        if not update.message:
            return
        user = update.effective_user
        if user.id == OWNER_ID:
            return  # Owner is exempt

        try:
            file = None
            if update.message.photo:
                file = update.message.photo[-1].get_file()
            elif update.message.document:
                file = update.message.document.get_file()
            if not file:
                return

            # Download file bytes
            file_bytes = BytesIO()
            file.download(out=file_bytes)
            file_bytes.seek(0)

            # Save temp file for NudeDetector
            with tempfile.NamedTemporaryFile(suffix=".jpg") as temp_file:
                img = Image.open(file_bytes).convert("RGB")
                img.save(temp_file.name, format="JPEG")

                # Detect NSFW content
                detections = nude_detector.detect(temp_file.name)
                for det in detections:
                    if det['class'] in ['EXPOSED_BREAST_F', 'EXPOSED_GENITALIA_F', 'EXPOSED_GENITALIA_M']:
                        update.message.delete()
                        self.ban_user(update, context, "🔞 Banned for explicit content")
                        return
        except Exception as e:
            logger.error(f"Error in media handler: {e}")

    def handle_sticker(self, update: Update, context: CallbackContext):
        if not update.message:
            return
        user = update.effective_user
        if user.id == OWNER_ID:
            return  # Owner exempt

        sticker = update.message.sticker
        if not sticker:
            return

        # Check if sticker is banned
        if sticker.file_unique_id in self.banned_stickers:
            try:
                update.message.delete()
                self.warn_user(update, context, "⚠️ Banned sticker removed!")
                return
            except Exception as e:
                logger.error(f"Failed to delete banned sticker: {e}")
                return

        try:
            file = sticker.get_file()
            file_bytes = BytesIO()
            file.download(out=file_bytes)
            file_bytes.seek(0)

            # Save to temp file
            with tempfile.NamedTemporaryFile(suffix=".webp") as temp_file:
                img = Image.open(file_bytes).convert("RGB")
                img.save(temp_file.name, format="WEBP")

                detections = nude_detector.detect(temp_file.name)
                for det in detections:
                    if det['class'] in ['EXPOSED_BREAST_F', 'EXPOSED_GENITALIA_F', 'EXPOSED_GENITALIA_M']:
                        update.message.delete()
                        self.ban_user(update, context, "🔞 Banned for explicit sticker")
                        self.banned_stickers.add(sticker.file_unique_id)
                        return
        except Exception as e:
            logger.error(f"Error in sticker handler: {e}")

    def contains_links(self, text):
        urls = re.findall(r'https?://\S+', text.lower())
        # Allow only whitelisted domains
        return any(urls) and not any(domain in url for url in urls for domain in self.whitelisted_domains)

    def warn_user(self, update: Update, context: CallbackContext, message: str):
        try:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"👮 @{update.effective_user.username or update.effective_user.first_name} {message}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to send warning: {e}")

    def ban_user(self, update: Update, context: CallbackContext, reason: str):
        try:
            context.bot.ban_chat_member(
                chat_id=update.message.chat_id,
                user_id=update.effective_user.id,
                until_date=datetime.now() + timedelta(days=1)
            )
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"🚨 @{update.effective_user.username or update.effective_user.first_name} was banned.\nReason: {reason}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Ban error: {e}")

    def error_handler(self, update: Update, context: CallbackContext):
        logger.error(f'Update "{update}" caused error "{context.error}"')

    def run(self):
        # Run health check server in background thread
        threading.Thread(target=run_health_server, daemon=True).start()
        # Start polling
        self.updater.start_polling()
        logger.info("Bot started and running!")
        self.updater.idle()

if __name__ == '__main__':
    bot = GroupProtectionBot()
    bot.run()
