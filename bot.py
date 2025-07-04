import os
import logging
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram import Update, ParseMode
from telegram.ext.callbackcontext import CallbackContext
import re
from datetime import datetime, timedelta
import cv2
import numpy as np
from io import BytesIO
from PIL import Image
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from nudenet import NudeDetector  # <-- Added NudeDetector import

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

# Initialize NudeDetector once globally
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
        
        # Security settings
        self.authorized_users = [OWNER_ID]  # Users allowed to post links
        self.whitelisted_domains = ['telegram.org', 'wikipedia.org', 'github.com']
        
        # Content filters
        self.promo_keywords = ['buy now', 'discount', 'promo', 'shop now']
        self.drug_keywords = ['weed', 'cocaine', 'heroin', 'drugs']
        self.weapon_keywords = ['gun', 'rifle', 'ammo', 'firearm']
        
        self.setup_handlers()
        logger.info("Bot initialized successfully")

    def setup_handlers(self):
        # Start command
        self.dp.add_handler(CommandHandler("start", self.send_welcome))
        
        # Admin commands
        self.dp.add_handler(CommandHandler("authorize", self.authorize_user))
        self.dp.add_handler(CommandHandler("unauthorize", self.unauthorize_user))
        
        # Message handlers - IMPORTANT: Set group=-100 to ensure handlers work in groups
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
        """Send welcome message when /start is used"""
        welcome_text = (
            "🛡️ *Group Protection Bot*\n\n"
            "Add me to your group as admin with these permissions:\n"
            "- Delete messages\n"
            "- Ban users\n\n"
            "I will automatically:\n"
            "- Delete all unauthorized links\n"
            "- Remove inappropriate media\n"
            "- Ban users who violate rules\n\n"
            "Owner commands:\n"
            "/authorize <user_id> - Allow user to post links\n"
            "/unauthorize <user_id> - Revoke link permissions"
        )
        update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

    def authorize_user(self, update: Update, context: CallbackContext):
        """Allow a user to post links"""
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
        """Revoke a user's link posting privileges"""
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

    def handle_text(self, update: Update, context: CallbackContext):
        """Handle all text messages including links"""
        if not update.message or not update.message.chat:
            return
            
        message = update.message.text or update.message.caption or ""
        user = update.effective_user
        
        # Check for links (applies to everyone except authorized users)
        if self.contains_links(message) and user.id not in self.authorized_users:
            try:
                update.message.delete()
                self.warn_user(update, context, "🔗 Links require authorization!")
            except Exception as e:
                logger.error(f"Failed to delete message: {e}")
            return
            
        # Check for bad content (applies to everyone)
        if self.contains_bad_content(message):
            try:
                update.message.delete()
                self.ban_user(update, context, "🚫 Banned for policy violation")
            except Exception as e:
                logger.error(f"Failed to delete bad content: {e}")

    def handle_media(self, update: Update, context: CallbackContext):
        """Analyze all photos and documents"""
        if not update.message or not update.message.chat:
            return
            
        user = update.effective_user
        if user.id in self.authorized_users:
            return
            
        try:
            # Get the file (photo or document)
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
            
            # Save temporarily for NudeDetector
            with open('temp_image', 'wb') as f:
                f.write(file_bytes.read())
            
            # Run NudeDetector on saved file
            results = nude_detector.detect('temp_image')
            
            # Check detection results
            for res in results:
                if res['class'] in ['EXPOSED_BREAST_F', 'EXPOSED_GENITALIA_F', 'EXPOSED_GENITALIA_M']:
                    update.message.delete()
                    self.ban_user(update, context, "🔞 Banned for explicit content")
                    return
            
        except Exception as e:
            logger.error(f"Error processing media: {e}")

    def handle_stickers(self, update: Update, context: CallbackContext):
        """Analyze all stickers and GIFs"""
        if not update.message or not update.message.chat:
            return
            
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
            file_bytes.seek(0)
            
            # Save sticker image temporarily
            with open('temp_sticker.webp', 'wb') as f:
                f.write(file_bytes.read())
            
            results = nude_detector.detect('temp_sticker.webp')
            
            for res in results:
                if res['class'] in ['EXPOSED_BREAST_F', 'EXPOSED_GENITALIA_F', 'EXPOSED_GENITALIA_M']:
                    update.message.delete()
                    self.ban_user(update, context, "🔞 Banned for explicit sticker")
                    return
                    
            # Also delete some inappropriate emoji stickers as before
            if sticker.emoji in ['💣', '🔫', '💊']:
                update.message.delete()
                self.warn_user(update, context, "⚠️ Inappropriate sticker removed!")
                
        except Exception as e:
            logger.error(f"Failed to process sticker: {e}")

    def contains_links(self, text):
        """Check for non-whitelisted URLs"""
        urls = re.findall(r'https?://\S+', text.lower())
        return any(urls) and not any(domain in url for url in urls for domain in self.whitelisted_domains)

    def contains_bad_content(self, text):
        """Check for prohibited keywords"""
        text_lower = text.lower()
        return (any(kw in text_lower for kw in self.promo_keywords) or
               any(kw in text_lower for kw in self.drug_keywords) or
               any(kw in text_lower for kw in self.weapon_keywords))

    def warn_user(self, update: Update, context: CallbackContext, message: str):
        """Send a warning to the chat"""
        try:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"👮 @{update.effective_user.username} {message}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to send warning: {e}")

    def ban_user(self, update: Update, context: CallbackContext, reason: str):
        """Ban a user temporarily"""
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
        """Log errors"""
        logger.error(f'Update "{update}" caused error "{context.error}"')

    def run(self):
        """Start the bot and web server"""
        # Start health check server in a separate thread
        server_thread = threading.Thread(target=run_health_server)
        server_thread.daemon = True
        server_thread.start()
        
        # Start the bot with proper group handlers
        self.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot is now running and protecting your groups!")
        self.updater.idle()

if __name__ == '__main__':
    bot = GroupSecurityBot()
    bot.run()
