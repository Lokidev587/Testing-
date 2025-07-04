import os
import logging
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram import ParseMode
import re
from datetime import datetime, timedelta
import tempfile
import cv2
import numpy as np
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from io import BytesIO
from PIL import Image

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

# Dummy web server for Render health checks
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running and protecting your group!')

def run_health_server():
    server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
    logger.info("Dummy web server running on port 8080")
    server.serve_forever()

class GroupProtectorBot:
    def __init__(self):
        self.updater = Updater(TELEGRAM_TOKEN, use_context=True)
        self.dp = self.updater.dispatcher
        
        # Security settings
        self.authorized_admins = []  # Admins who can post links
        self.whitelisted_domains = ['telegram.org', 'wikipedia.org', 'github.com']
        
        # Content filters
        self.promo_keywords = ['buy now', 'discount', 'promo code', 'shop now']
        self.drug_keywords = ['weed', 'cocaine', 'heroin', 'drugs']
        self.weapon_keywords = ['gun', 'rifle', 'ammo', 'firearm']
        self.terror_keywords = ['isis', 'terrorism', 'bomb']
        
        self.setup_handlers()
        logger.info("Bot initialized successfully")

    def setup_handlers(self):
        # Start command
        self.dp.add_handler(CommandHandler("start", self.send_welcome))
        
        # Admin commands
        self.dp.add_handler(CommandHandler("approve_admin", self.approve_admin))
        self.dp.add_handler(CommandHandler("revoke_admin", self.revoke_admin))
        
        # Message handlers
        self.dp.add_handler(MessageHandler(
            Filters.text | Filters.caption, 
            self.handle_text_messages
        ))
        self.dp.add_handler(MessageHandler(
            Filters.photo | Filters.document, 
            self.handle_media
        ))
        self.dp.add_handler(MessageHandler(
            Filters.sticker | Filters.animation, 
            self.handle_stickers_gifs
        ))
        
        # Error handler
        self.dp.add_error_handler(self.error_handler)

    def send_welcome(self, update, context):
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
            "/approve_admin <user_id> - Allow admin to post links\n"
            "/revoke_admin <user_id> - Revoke link permissions"
        )
        update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

    def approve_admin(self, update, context):
        """Allow an admin to post links"""
        if update.message.from_user.id != OWNER_ID:
            update.message.reply_text("❌ Only owner can approve admins.")
            return
            
        try:
            admin_id = int(context.args[0])
            if admin_id not in self.authorized_admins:
                self.authorized_admins.append(admin_id)
                update.message.reply_text(f"✅ Admin {admin_id} can now post links.")
            else:
                update.message.reply_text("ℹ️ Admin is already approved.")
        except (IndexError, ValueError):
            update.message.reply_text("⚠️ Usage: /approve_admin <admin_id>")

    def revoke_admin(self, update, context):
        """Revoke an admin's link posting privileges"""
        if update.message.from_user.id != OWNER_ID:
            update.message.reply_text("❌ Only owner can revoke admin privileges.")
            return
            
        try:
            admin_id = int(context.args[0])
            if admin_id in self.authorized_admins:
                self.authorized_admins.remove(admin_id)
                update.message.reply_text(f"✅ Admin {admin_id} can no longer post links.")
            else:
                update.message.reply_text("ℹ️ Admin wasn't approved.")
        except (IndexError, ValueError):
            update.message.reply_text("⚠️ Usage: /revoke_admin <admin_id>")

    def handle_text_messages(self, update, context):
        """Handle all text messages including links"""
        if not update.message:
            return
            
        message = update.message.text or update.message.caption or ""
        user = update.message.from_user
        
        # Check if sender is admin (bot or human)
        is_admin = False
        if user.id == context.bot.id:
            is_admin = True
        else:
            chat_member = update.message.chat.get_member(user.id)
            is_admin = chat_member.status in ['administrator', 'creator']
        
        # Check for links (applies to everyone except owner and approved admins)
        if (self.contains_links(message) and 
            user.id != OWNER_ID and 
            user.id not in self.authorized_admins and
            is_admin):
            update.message.delete()
            self.warn_user(update, context, "🔗 Links require owner approval!")
            return
            
        # Check for illegal/dangerous content (applies to everyone)
        if self.contains_bad_content(message):
            update.message.delete()
            self.ban_user(update, context, "🚫 Banned for policy violation")

    def handle_media(self, update, context):
        """Analyze all photos and documents"""
        if not update.message:
            return
            
        user = update.message.from_user
        if user.id == OWNER_ID:
            return
            
        try:
            # Get the file (photo or document)
            file = None
            if update.message.photo:
                file = context.bot.get_file(update.message.photo[-1].file_id)
            elif update.message.document:
                file = context.bot.get_file(update.message.document.file_id)
                
            if not file:
                return
                
            # Download and analyze the file
            file_bytes = BytesIO()
            file.download(out=file_bytes)
            file_bytes.seek(0)
            
            # Use Pillow to open image
            img = Image.open(file_bytes)
            
            # Convert to numpy array for OpenCV
            img_cv = np.array(img)
            if len(img_cv.shape) == 2:  # Grayscale
                img_cv = cv2.cvtColor(img_cv, cv2.COLOR_GRAY2BGR)
            elif img_cv.shape[2] == 4:  # RGBA
                img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGBA2BGR)
            else:  # RGB
                img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGB2BGR)
            
            # Check for explicit content
            if self.detect_explicit_content(img_cv):
                update.message.delete()
                self.ban_user(update, context, "🔞 Banned for explicit content")
                return
                
            # Check for weapons
            if self.detect_weapons(img_cv):
                update.message.delete()
                self.ban_user(update, context, "🔫 Banned for weapon content")
                return
                
        except Exception as e:
            logger.error(f"Error processing media: {e}")

    def handle_stickers_gifs(self, update, context):
        """Analyze all stickers and GIFs"""
        if not update.message:
            return
            
        user = update.message.from_user
        if user.id == OWNER_ID:
            return
            
        sticker = update.message.sticker
        if sticker and sticker.emoji in ['💣', '🔫', '💊']:
            update.message.delete()
            self.warn_user(update, context, "⚠️ Inappropriate sticker detected!")

    def contains_links(self, text):
        """Check for non-whitelisted URLs"""
        urls = re.findall(r'https?://\S+', text.lower())
        return any(urls) and not any(domain in url for url in urls for domain in self.whitelisted_domains)

    def contains_bad_content(self, text):
        """Check for prohibited keywords"""
        text_lower = text.lower()
        return (any(kw in text_lower for kw in self.promo_keywords) or
               any(kw in text_lower for kw in self.drug_keywords) or
               any(kw in text_lower for kw in self.weapon_keywords) or
               any(kw in text_lower for kw in self.terror_keywords))

    def detect_explicit_content(self, img):
        """Basic explicit content detection"""
        try:
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            lower_skin = np.array([0, 48, 80], dtype=np.uint8)
            upper_skin = np.array([20, 255, 255], dtype=np.uint8)
            mask = cv2.inRange(hsv, lower_skin, upper_skin)
            skin_ratio = cv2.countNonZero(mask) / (img.shape[0] * img.shape[1])
            return skin_ratio > 0.3
        except Exception as e:
            logger.error(f"Explicit content detection error: {e}")
            return False

    def detect_weapons(self, img):
        """Basic weapon detection"""
        try:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 100, 200)
            contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                approx = cv2.approxPolyDP(cnt, 0.01*cv2.arcLength(cnt, True), True)
                if len(approx) in [4, 5, 6] and cv2.contourArea(cnt) > 500:
                    return True
            return False
        except Exception as e:
            logger.error(f"Weapon detection error: {e}")
            return False

    def warn_user(self, update, context, message):
        """Send a warning to the chat"""
        user = update.message.from_user
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"👮 @{user.username} {message}",
            parse_mode=ParseMode.MARKDOWN
        )

    def ban_user(self, update, context, reason):
        """Ban a user temporarily"""
        user = update.message.from_user
        try:
            context.bot.ban_chat_member(
                chat_id=update.message.chat_id,
                user_id=user.id,
                until_date=datetime.now() + timedelta(days=1))
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"🚨 @{user.username} was banned. Reason: {reason}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Ban error: {e}")

    def error_handler(self, update, context):
        """Log errors"""
        logger.error(f'Update "{update}" caused error "{context.error}"')

    def run(self):
        """Start the bot and web server"""
        # Start health check server in a separate thread
        server_thread = threading.Thread(target=run_health_server)
        server_thread.daemon = True
        server_thread.start()
        
        # Start the bot
        self.updater.start_polling()
        logger.info("Bot is now running and protecting your groups!")
        self.updater.idle()

if __name__ == '__main__':
    bot = GroupProtectorBot()
    bot.run()
