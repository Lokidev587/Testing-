import os
import logging
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from telegram import Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackContext
)
from nudenet import NudeDetector

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize NSFW detector
detector = NudeDetector()

# Bot configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
AUTHORIZED_USERS = {}  # {chat_id: [user_ids]}
GROUP_ADMINS = {}      # {chat_id: [user_ids]}
GROUP_OWNERS = {}      # {chat_id: user_id}

# NSFW classes to detect
NSFW_CLASSES = [
    "FEMALE_GENITALIA_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "ANUS_EXPOSED"
]

# Dummy HTTP server for Render
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_dummy_server(port=8080):
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

async def refresh_admins(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Refresh admin list for a chat"""
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        admin_ids = [admin.user.id for admin in admins]
        owner = next((admin for admin in admins if admin.status == ChatMemberStatus.OWNER), None)
        
        if owner:
            GROUP_OWNERS[chat_id] = owner.user.id
            GROUP_ADMINS[chat_id] = admin_ids
            if chat_id not in AUTHORIZED_USERS:
                AUTHORIZED_USERS[chat_id] = admin_ids.copy()
            return True
        return False
    except Exception as e:
        logger.error(f"Error refreshing admins: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Hi! I will help manage links and NSFW content in this group.')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
    Group Management Bot Help:
    
    - I automatically detect and delete unauthorized links shared by non-admin users
    - I detect and delete NSFW content (images, GIFs, stickers) with warnings
    - Only group owners can authorize users to post links
    
    Commands:
    /start - Start the bot
    /help - Show this help message
    /authorize @username - Authorize a user to post links (owner only)
    /unauthorize @username - Remove user's link posting privileges (owner only)
    """
    await update.message.reply_text(help_text)

async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_message is None:
            return

        chat = update.effective_message.chat
        chat_id = chat.id

        if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
            return

        await refresh_admins(chat_id, context)
    except Exception as e:
        logger.error(f"Error tracking chats: {e}")

async def authorize_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        # Refresh admin list first
        if not await refresh_admins(chat_id, context):
            await update.message.reply_text("Could not fetch admin information. Please try again.")
            return
        
        # Verify ownership
        if chat_id not in GROUP_OWNERS or GROUP_OWNERS[chat_id] != user_id:
            await update.message.reply_text("Only the group owner can authorize users.")
            return
        
        if not context.args:
            await update.message.reply_text("Please specify a username to authorize (e.g., /authorize @username).")
            return
        
        username = context.args[0].strip('@')
        
        try:
            member = await context.bot.get_chat_member(chat_id, username)
            target_user_id = member.user.id
            
            # Initialize if not exists
            if chat_id not in AUTHORIZED_USERS:
                AUTHORIZED_USERS[chat_id] = []
            
            if target_user_id not in AUTHORIZED_USERS[chat_id]:
                AUTHORIZED_USERS[chat_id].append(target_user_id)
                await update.message.reply_text(f"User @{username} is now authorized to post links.")
            else:
                await update.message.reply_text(f"User @{username} is already authorized.")
                
        except Exception as e:
            logger.error(f"Error authorizing user: {e}")
            await update.message.reply_text("Could not find that user. Please make sure you've entered the correct username.")
    except Exception as e:
        logger.error(f"Error in authorize_user: {e}")

async def unauthorize_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        # Refresh admin list first
        if not await refresh_admins(chat_id, context):
            await update.message.reply_text("Could not fetch admin information. Please try again.")
            return
        
        # Verify ownership
        if chat_id not in GROUP_OWNERS or GROUP_OWNERS[chat_id] != user_id:
            await update.message.reply_text("Only the group owner can unauthorize users.")
            return
        
        if not context.args:
            await update.message.reply_text("Please specify a username to unauthorize (e.g., /unauthorize @username).")
            return
        
        username = context.args[0].strip('@')
        
        try:
            member = await context.bot.get_chat_member(chat_id, username)
            target_user_id = member.user.id
            
            if chat_id in AUTHORIZED_USERS and target_user_id in AUTHORIZED_USERS[chat_id]:
                AUTHORIZED_USERS[chat_id].remove(target_user_id)
                await update.message.reply_text(f"User @{username} is no longer authorized to post links.")
            else:
                await update.message.reply_text(f"User @{username} wasn't authorized.")
                
        except Exception as e:
            logger.error(f"Error unauthorizing user: {e}")
            await update.message.reply_text("Could not find that user. Please make sure you've entered the correct username.")
    except Exception as e:
        logger.error(f"Error in unauthorize_user: {e}")

async def delete_message(context: CallbackContext):
    try:
        await context.bot.delete_message(context.job.chat_id, context.job.message_id)
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

def is_nsfw(detections):
    return any(detection['class'] in NSFW_CLASSES and detection['score'] > 0.7 for detection in detections)

async def handle_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.effective_message or not update.effective_chat:
            return
        
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        # Skip if no links or authorized
        if not update.effective_message.entities or not any(
            e.type == "url" for e in update.effective_message.entities
        ):
            return
        
        # Check authorization
        is_authorized = (
            (chat_id in AUTHORIZED_USERS and user_id in AUTHORIZED_USERS[chat_id]) or
            (chat_id in GROUP_ADMINS and user_id in GROUP_ADMINS[chat_id]) or
            (chat_id in GROUP_OWNERS and user_id == GROUP_OWNERS[chat_id])
        )
        if is_authorized:
            return
        
        try:
            await update.effective_message.delete()
            warning = await update.effective_chat.send_message(
                f"@{update.effective_user.username} Only authorized users can post links. "
                "Contact the group owner for authorization."
            )
            context.job_queue.run_once(
                delete_message, 10,
                chat_id=chat_id,
                message_id=warning.message_id,
                name=str(warning.message_id)
        except Exception as e:
            logger.error(f"Error handling links: {e}")
    except Exception as e:
        logger.error(f"Error in handle_links: {e}")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.effective_message or not update.effective_chat:
            return
        
        if not any([
            update.effective_message.photo,
            update.effective_message.animation,
            update.effective_message.sticker,
            update.effective_message.video,
            update.effective_message.document
        ]):
            return
        
        try:
            file = await context.bot.get_file(update.effective_message.effective_attachment.file_id)
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
                file_path = temp_file.name
                await file.download_to_drive(file_path)
                
                try:
                    detections = detector.detect(file_path)
                    if is_nsfw(detections):
                        await update.effective_message.delete()
                        warning = await update.effective_chat.send_message(
                            f"@{update.effective_user.username} NSFW content detected and removed. "
                            "Please keep the group safe for work."
                        )
                        context.job_queue.run_once(
                            delete_message, 
                            10,
                            chat_id=update.effective_chat.id,
                            message_id=warning.message_id,
                            name=str(warning.message_id)
                        )
                finally:
                    try:
                        os.unlink(file_path)
                    except Exception as e:
                        logger.error(f"Error deleting temp file: {e}")
        except Exception as e:
            logger.error(f"Error handling media: {e}")
    except Exception as e:
        logger.error(f"Error in handle_media: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "An error occurred. Please try again or contact support."
            )
        except Exception as e:
            logger.error(f"Error sending error message: {e}")

def main():
    # Start dummy server if on Render
    if os.getenv('RENDER'):
        port = int(os.getenv('PORT', 8080))
        server_thread = threading.Thread(target=run_dummy_server, args=(port,))
        server_thread.daemon = True
        server_thread.start()
        logger.info(f"Dummy server started on port {port}")

    # Create and configure application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    handlers = [
        CommandHandler("start", start),
        CommandHandler("help", help_command),
        CommandHandler("authorize", authorize_user),
        CommandHandler("unauthorize", unauthorize_user),
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, track_chats),
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_links),
        MessageHandler(
            filters.PHOTO | filters.ANIMATION | filters.Sticker.ALL | 
            filters.VIDEO | filters.Document.ALL,
            handle_media
        )
    ]
    
    for handler in handlers:
        application.add_handler(handler)
    
    application.add_error_handler(error_handler)
    
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
