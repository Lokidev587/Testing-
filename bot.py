import os
import logging
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram import ParseMode, ChatPermissions
import re
from datetime import datetime, timedelta
import tempfile
import cv2
import numpy as np

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
OWNER_ID = 8122582244  # Your Telegram user ID
TELEGRAM_TOKEN = "7951953732:AAGEsXUyxbkjs5BKouKwcecGCLZ22cD0wuw"  # Your bot token

class AdvancedGroupProtector:
    def __init__(self):
        self.updater = Updater(TELEGRAM_TOKEN, use_context=True)
        self.dp = self.updater.dispatcher
        
        # Security settings
        self.authorized_admins = []  # Admins who can post links
        self.whitelisted_domains = ['telegram.org', 'wikipedia.org', 'github.com']
        
        # Content detection patterns
        self.promo_keywords = ['buy now', 'discount', 'promo code', 'limited offer', 'click here']
        self.drug_keywords = ['weed', 'cocaine', 'heroin', 'drugs', 'mdma', 'meth', 'opium']
        self.weapon_keywords = ['gun', 'rifle', 'ammo', 'firearm', 'weapon', 'bomb']
        self.terror_keywords = ['isis', 'al-qaeda', 'terrorism', 'taliban']
        
        # Known bad content hashes (would be populated from a database in production)
        self.banned_sticker_hashes = set()
        self.banned_gif_hashes = set()
        
        self.setup_handlers()
    
    def setup_handlers(self):
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
    
    # ========== ADMIN COMMANDS ==========
    def approve_admin(self, update, context):
        """Allow an admin to post links"""
        if update.message.from_user.id != OWNER_ID:
            update.message.reply_text("❌ Only the owner can approve admins.")
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
            update.message.reply_text("❌ Only the owner can revoke admin privileges.")
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
    
    # ========== MESSAGE HANDLERS ==========
    def handle_text_messages(self, update, context):
        """Handle all text messages including links"""
        if not update.message: return
        
        message = update.message.text or update.message.caption or ""
        user = update.message.from_user
        chat = update.message.chat
        
        # Check if sender is admin (bot or human)
        is_admin = False
        if user.id == context.bot.id:
            is_admin = True
        else:
            chat_member = chat.get_member(user.id)
            is_admin = chat_member.status in ['administrator', 'creator']
        
        # Check for links (applies to everyone except owner and approved admins)
        if (self.contains_links(message) and 
            user.id != OWNER_ID and 
            user.id not in self.authorized_admins):
            update.message.delete()
            self.warn_user(update, context, "🔗 Links require owner approval!")
            return
            
        # Check for illegal/dangerous content (applies to everyone)
        if self.contains_bad_content(message):
            update.message.delete()
            self.ban_user(update, context, "🚫 Banned for illegal content")
            return
    
    def handle_media(self, update, context):
        """Analyze all photos and documents"""
        if not update.message: return
        
        user = update.message.from_user
        chat = update.message.chat
        
        # Skip if from owner
        if user.id == OWNER_ID:
            return
            
        # Get the file (photo or document)
        file = None
        if update.message.photo:
            file = context.bot.get_file(update.message.photo[-1].file_id)
        elif update.message.document:
            file = context.bot.get_file(update.message.document.file_id)
            
        if not file: return
        
        # Download and analyze the file
        with tempfile.NamedTemporaryFile() as tmp:
            file.download(out=tmp)
            tmp.seek(0)
            
            # Check for explicit content
            if self.detect_explicit_content(tmp.name):
                update.message.delete()
                self.ban_user(update, context, "🔞 Banned for explicit content")
                return
                
            # Check for weapons
            if self.detect_weapons(tmp.name):
                update.message.delete()
                self.ban_user(update, context, "🔫 Banned for weapon content")
                return
    
    def handle_stickers_gifs(self, update, context):
        """Analyze all stickers and GIFs"""
        if not update.message: return
        
        user = update.message.from_user
        if user.id == OWNER_ID:
            return
            
        file = None
        if update.message.sticker:
            file = context.bot.get_file(update.message.sticker.file_id)
        elif update.message.animation:
            file = context.bot.get_file(update.message.animation.file_id)
            
        if not file: return
            
        # Check against known bad content hashes
        file_hash = self.get_file_hash(file)
        if (file_hash in self.banned_sticker_hashes or 
            file_hash in self.banned_gif_hashes):
            update.message.delete()
            self.ban_user(update, context, "🚫 Banned for inappropriate media")
            return
    
    # ========== CONTENT DETECTION METHODS ==========
    def contains_links(self, text):
        """Check for non-whitelisted URLs"""
        urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', text)
        return any(urls) and not any(domain in url for url in urls for domain in self.whitelisted_domains)
    
    def contains_bad_content(self, text):
        """Check for prohibited keywords"""
        text_lower = text.lower()
        return (any(kw in text_lower for kw in self.promo_keywords) or
                any(kw in text_lower for kw in self.drug_keywords) or
                any(kw in text_lower for kw in self.weapon_keywords) or
                any(kw in text_lower for kw in self.terror_keywords))
    
    def detect_explicit_content(self, file_path):
        """Basic explicit content detection using OpenCV"""
        try:
            image = cv2.imread(file_path)
            if image is None: return False
            
            # Convert to HSV color space
            hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
            
            # Define skin color range
            lower_skin = np.array([0, 48, 80], dtype=np.uint8)
            upper_skin = np.array([20, 255, 255], dtype=np.uint8)
            
            # Threshold the HSV image
            mask = cv2.inRange(hsv, lower_skin, upper_skin)
            
            # Calculate percentage of skin pixels
            skin_pixels = cv2.countNonZero(mask)
            total_pixels = image.shape[0] * image.shape[1]
            skin_ratio = skin_pixels / total_pixels
            
            return skin_ratio > 0.3  # Threshold for explicit content
        except:
            return False
    
    def detect_weapons(self, file_path):
        """Basic weapon detection using contour analysis"""
        try:
            image = cv2.imread(file_path, 0)  # Read as grayscale
            if image is None: return False
            
            # Edge detection
            edges = cv2.Canny(image, 100, 200)
            
            # Find contours
            contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            
            # Analyze contours for weapon-like shapes
            for cnt in contours:
                approx = cv2.approxPolyDP(cnt, 0.01*cv2.arcLength(cnt, True), True)
                if len(approx) in [4, 5, 6]:  # Simple shape detection
                    area = cv2.contourArea(cnt)
                    if area > 500:  # Minimum size threshold
                        return True
            return False
        except:
            return False
    
    def get_file_hash(self, file):
        """Generate a simple hash for media files"""
        file_bytes = file.download_as_bytearray()
        return hash(bytes(file_bytes))
    
    # ========== MODERATION ACTIONS ==========
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
                until_date=datetime.now() + timedelta(days=7))
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"🚨 @{user.username} was banned. Reason: {reason}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Ban error: {e}")
    
    def error_handler(self, update, context):
        """Log errors"""
        logger.warning(f'Error: {context.error}')
    
    def run(self):
        """Start the bot"""
        self.updater.start_polling()
        logger.info("Bot is running and protecting your groups!")
        self.updater.idle()

if __name__ == '__main__':
    protector = AdvancedGroupProtector()
    protector.run()