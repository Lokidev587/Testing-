import os
import logging
import requests
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram import Update, ParseMode
from telegram.ext.callbackcontext import CallbackContext
import re
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import numpy as np
from nudenet import NudeDetector

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', '8122582244'))  # Your Telegram ID

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable not set!")

# Initialize NudeDetector
nude_detector = NudeDetector(
 model_path="https://huggingface.co/MultimodalART/NudeNet/resolve/main/detector_v2_default_checkpoint.onnx"     
)

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
        
        # Security settings
        self.authorized_users = [OWNER_ID]
        self.whitelisted_domains = ['telegram.org', 'wikipedia.org', 'github.com']
        
        # Content filters
        self.promo_keywords = ['buy now', 'discount', 'promo', 'shop now']
        self.drug_keywords = ['weed', 'cocaine', 'heroin', 'drugs']
        self.weapon_keywords = ['gun', 'rifle', 'ammo', 'firearm']
        
        # Banned sticker file_unique_ids (more reliable than hashes)
        self.banned_stickers = set()
        
        self.setup_handlers()
        logger.info("Bot initialized successfully")

    def setup_handlers(self):
        # Start command
        self.dp.add_handler(CommandHandler("start", self.send_welcome))
        
        # Admin commands
        self.dp.add_handler(CommandHandler("authorize", self.authorize_user))
        self.dp.add_handler(CommandHandler("unauthorize", self.unauthorize_user))
        self.dp.add_handler(CommandHandler("ban_sticker", self.ban_sticker))
        
        # Message handlers
        self.dp.add_handler(MessageHandler(
            Filters.text | Filters.caption, 
            self.handle_text,
            run_async=True
        ), group=-100)
        
        self.dp.add_handler(MessageHandler(
            Filters.photo | Filters.document, 
            self.handle_media,
            run_async=True
        ), group=-100)
        
        self.dp.add_handler(MessageHandler(
            Filters.sticker | Filters.animation, 
            self.handle_stickers,
            run_async=True
        ), group=-100)
        
        # Error handler
        self.dp.add_error_handler(self.error_handler)

    def send_welcome(self, update: Update, context: CallbackContext):
        welcome_text = (
            "🛡️ *Group Protection Bot*\n\n"
            "Add me to your group as admin with these permissions:\n"
            "- Delete messages\n"
            "- Ban users\n\n"
            "I will automatically:\n"
            "- Delete all unauthorized links\n"
            "- Remove inappropriate media and stickers\n"
            "- Ban users who violate rules\n\n"
            "Owner commands:\n"
            "/authorize <user_id> - Allow user to post links\n"
            "/unauthorize <user_id> - Revoke link permissions\n"
            "/ban_sticker - Reply to a sticker to ban it"
        )
        update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

    def authorize_user(self, update: Update, context: CallbackContext):
        if update.effective_user.id != OWNER_ID:
            update.message.reply_text("❌ Only owner can authorize users.")
            return
            
        try:
            user_id = int(context.args[0])
            if user_id not in self.authorized_users:
                self.authorized_users.append(user_id)
                update.message.reply_text(f"✅ User {user_id} can now post links.")
            else:
                update.message.reply_text("ℹ️ User is already authorized.")
        except (IndexError, ValueError):
            update.message.reply_text("⚠️ Usage: /authorize <user_id>")

    def unauthorize_user(self, update: Update, context: CallbackContext):
        if update.effective_user.id != OWNER_ID:
            update.message.reply_text("❌ Only owner can unauthorize users.")
            return
            
        try:
            user_id = int(context.args[0])
            if user_id in self.authorized_users:
                self.authorized_users.remove(user_id)
                update.message.reply_text(f"✅ User {user_id} can no longer post links.")
            else:
                update.message.reply_text("ℹ️ User wasn't authorized.")
        except (IndexError, ValueError):
            update.message.reply_text("⚠️ Usage: /unauthorize <user_id>")

    def ban_sticker(self, update: Update, context: CallbackContext):
        if update.effective_user.id != OWNER_ID:
            update.message.reply_text("❌ Only owner can ban stickers.")
            return
            
        if not update.message.reply_to_message or not update.message.reply_to_message.sticker:
            update.message.reply_text("⚠️ Please reply to a sticker to ban it.")
            return
            
        sticker = update.message.reply_to_message.sticker
        self.banned_stickers.add(sticker.file_unique_id)
        update.message.reply_text(f"✅ Sticker banned (ID: {sticker.file_unique_id})")

    def handle_text(self, update: Update, context: CallbackContext):
        if not update.message or not update.message.chat:
            return
            
        message = update.message.text or update.message.caption or ""
        user = update.effective_user
        
        if self.contains_links(message) and user.id not in self.authorized_users:
            try:
                update.message.delete()
                self.warn_user(update, context, "🔗 Links require authorization!")
            except Exception as e:
                logger.error(f"Failed to delete message: {e}")
            return
            
        if self.contains_bad_content(message):
            try:
                update.message.delete()
                self.ban_user(update, context, "🚫 Banned for policy violation")
            except Exception as e:
                logger.error(f"Failed to delete bad content: {e}")

    def handle_media(self, update: Update, context: CallbackContext):
        if not update.message or not update.message.chat:
            return
            
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
            file_bytes.seek(0)
            
            # Save to temp file for NudeNet
            with tempfile.NamedTemporaryFile(suffix='.jpg') as temp:
                img = Image.open(file_bytes)
                img.save(temp.name, format='JPEG')
                
                # Detect NSFW content
                detections = nude_detector.detect(temp.name)
                
                # Check for explicit content
                for detection in detections:
                    if detection['class'] in ['EXPOSED_BREAST_F', 'EXPOSED_GENITALIA_F', 'EXPOSED_GENITALIA_M']:
                        update.message.delete()
                        self.ban_user(update, context, "🔞 Banned for explicit content")
                        return
                        
        except Exception as e:
            logger.error(f"Error processing media: {e}")

    def handle_stickers(self, update: Update, context: CallbackContext):
        if not update.message or not update.message.chat:
            return
            
        user = update.effective_user
        if user.id in self.authorized_users:
            return
            
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
                logger.error(f"Failed to delete sticker: {e}")
                return
                
        # Download and check sticker with NudeNet
        try:
            file = sticker.get_file()
            file_bytes = BytesIO()
            file.download(out=file_bytes)
            file_bytes.seek(0)
            
            # Save to temp file
            with tempfile.NamedTemporaryFile(suffix='.webp') as temp:
                img = Image.open(file_bytes)
                img.save(temp.name, format='WEBP')
                
                # Detect NSFW content
                detections = nude_detector.detect(temp.name)
                
                # Check for explicit content
                for detection in detections:
                    if detection['class'] in ['EXPOSED_BREAST_F', 'EXPOSED_GENITALIA_F', 'EXPOSED_GENITALIA_M']:
                        update.message.delete()
                        self.ban_user(update, context, "🔞 Banned for explicit sticker")
                        # Add to banned stickers
                        self.banned_stickers.add(sticker.file_unique_id)
                        return
                        
        except Exception as e:
            logger.error(f"Error processing sticker: {e}")

    def contains_links(self, text):
        urls = re.findall(r'https?://\S+', text.lower())
        return any(urls) and not any(domain in url for url in urls for domain in self.whitelisted_domains)

    def contains_bad_content(self, text):
        text_lower = text.lower()
        return (any(kw in text_lower for kw in self.promo_keywords) or
               any(kw in text_lower for kw in self.drug_keywords) or
               any(kw in text_lower for kw in self.weapon_keywords))

    def warn_user(self, update: Update, context: CallbackContext, message: str):
        try:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"👮 @{update.effective_user.username} {message}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to send warning: {e}")

    def ban_user(self, update: Update, context: CallbackContext, reason: str):
        try:
            context.bot.ban_chat_member(
                chat_id=update.message.chat_id,
                user_id=update.effective_user.id,
                until_date=datetime.now() + timedelta(days=1))
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"🚨 @{update.effective_user.username} was banned. Reason: {reason}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Ban error: {e}")

    def error_handler(self, update: Update, context: CallbackContext):
        logger.error(f'Update "{update}" caused error "{context.error}"')

    def run(self):
        # Start health check server
        server_thread = threading.Thread(target=run_health_server)
        server_thread.daemon = True
        server_thread.start()
        
        # Start the bot
        self.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot is now running and protecting your groups!")
        self.updater.idle()

if __name__ == '__main__':
    bot = GroupSecurityBot()
    bot.run()
