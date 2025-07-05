import os
import logging
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
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

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
AUTHORIZED_USERS = {}  # Format: {chat_id: [user_ids]}
GROUP_ADMINS = {}      # Format: {chat_id: [user_ids]}
GROUP_OWNERS = {}      # Format: {chat_id: user_id}

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text('Hi! I will help manage links and NSFW content in this group.')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
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
    """Track which chats the bot is in and store admin/owner info."""
    try:
        if update.effective_message is None:
            return

        chat = update.effective_message.chat
        chat_id = chat.id

        if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
            return

        # Get chat administrators
        admins = await context.bot.get_chat_administrators(chat_id)
        admin_ids = [admin.user.id for admin in admins]
        
        # Find the owner
        owner = next((admin for admin in admins if admin.status == ChatMemberStatus.OWNER), None)
        
        if owner:
            GROUP_OWNERS[chat_id] = owner.user.id
            GROUP_ADMINS[chat_id] = admin_ids
            
            if chat_id not in AUTHORIZED_USERS:
                AUTHORIZED_USERS[chat_id] = admin_ids.copy()
            
            logger.info(f"Tracked chat {chat_id} with owner {owner.user.id}")
    except Exception as e:
        logger.error(f"Error tracking chats: {e}")

async def authorize_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Authorize a user to post links."""
    try:
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        # Check if the command user is the owner
        if chat_id not in GROUP_OWNERS or GROUP_OWNERS[chat_id] != user_id:
            await update.message.reply_text("Only the group owner can authorize users.")
            return
        
        if not context.args:
            await update.message.reply_text("Please specify a username to authorize (e.g., /authorize @username).")
            return
        
        username = context.args[0].strip('@')
        
        try:
            # Get user ID from username
            member = await context.bot.get_chat_member(chat_id, username)
            target_user_id = member.user.id
            
            # Add to authorized users
            if chat_id in AUTHORIZED_USERS:
                if target_user_id not in AUTHORIZED_USERS[chat_id]:
                    AUTHORIZED_USERS[chat_id].append(target_user_id)
                    await update.message.reply_text(f"User @{username} is now authorized to post links.")
                else:
                    await update.message.reply_text(f"User @{username} is already authorized.")
            else:
                AUTHORIZED_USERS[chat_id] = [target_user_id]
                await update.message.reply_text(f"User @{username} is now authorized to post links.")
                
        except Exception as e:
            logger.error(f"Error authorizing user: {e}")
            await update.message.reply_text("Could not find that user. Please make sure you've entered the correct username.")
    except Exception as e:
        logger.error(f"Error in authorize_user: {e}")

async def unauthorize_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a user's authorization to post links."""
    try:
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        # Check if the command user is the owner
        if chat_id not in GROUP_OWNERS or GROUP_OWNERS[chat_id] != user_id:
            await update.message.reply_text("Only the group owner can unauthorize users.")
            return
        
        if not context.args:
            await update.message.reply_text("Please specify a username to unauthorize (e.g., /unauthorize @username).")
            return
        
        username = context.args[0].strip('@')
        
        try:
            # Get user ID from username
            member = await context.bot.get_chat_member(chat_id, username)
            target_user_id = member.user.id
            
            # Remove from authorized users
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
    """Delete a message."""
    try:
        chat_id = context.job.chat_id
        message_id = context.job.message_id
        await context.bot.delete_message(chat_id, message_id)
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

def is_nsfw(detections):
    """Check if any detection is NSFW based on our defined classes."""
    for detection in detections:
        if detection['class'] in NSFW_CLASSES and detection['score'] > 0.7:  # 70% confidence threshold
            return True
    return False

async def handle_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages containing links."""
    try:
        if update.effective_message is None or update.effective_chat is None:
            return
        
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        # Skip if message doesn't contain links or user is authorized/admin/owner
        if not update.effective_message.entities:
            return
        
        # Check for URL entities
        has_links = any(entity.type == "url" for entity in update.effective_message.entities)
        if not has_links:
            return
        
        # Allow links from authorized users, admins, and owner
        if (chat_id in AUTHORIZED_USERS and user_id in AUTHORIZED_USERS[chat_id]) or \
           (chat_id in GROUP_ADMINS and user_id in GROUP_ADMINS[chat_id]) or \
           (chat_id in GROUP_OWNERS and user_id == GROUP_OWNERS[chat_id]):
            return
        
        # Delete the message with links
        try:
            await update.effective_message.delete()
            warning = await update.effective_chat.send_message(
                f"@{update.effective_user.username} Only authorized users can post links. "
                "Contact the group owner for authorization."
            )
            
            # Delete warning after 10 seconds
            context.job_queue.run_once(
                delete_message,
                10,
                chat_id=chat_id,
                message_id=warning.message_id,
                name=str(warning.message_id)
            )
            
        except Exception as e:
            logger.error(f"Error deleting message: {e}")
    except Exception as e:
        logger.error(f"Error in handle_links: {e}")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle media messages and check for NSFW content."""
    try:
        if update.effective_message is None or update.effective_chat is None:
            return
        
        # Skip if message doesn't contain media
        if not (update.effective_message.photo or 
                update.effective_message.animation or 
                update.effective_message.sticker or 
                update.effective_message.video or 
                update.effective_message.document):
            return
        
        try:
            # Download the media file
            file = await context.bot.get_file(update.effective_message.effective_attachment.file_id)
            file_bytes = await file.download_as_bytearray()
            
            # Create a temporary file
            with tempfile.NamedTemporaryFile(suffix='.jpg') as temp_file:
                temp_file.write(file_bytes)
                temp_file.flush()
                
                # Detect NSFW content
                detections = detector.detect(temp_file.name)
                
                # Check if any NSFW content was detected
                if is_nsfw(detections):
                    await update.effective_message.delete()
                    warning = await update.effective_chat.send_message(
                        f"@{update.effective_user.username} NSFW content detected and removed. "
                        "Please keep the group safe for work."
                    )
                    
                    # Delete warning after 10 seconds
                    context.job_queue.run_once(
                        delete_message,
                        10,
                        chat_id=update.effective_chat.id,
                        message_id=warning.message_id,
                        name=str(warning.message_id)
                    )
        
        except Exception as e:
            logger.error(f"Error handling media: {e}")
    except Exception as e:
        logger.error(f"Error in handle_media: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and send a message if possible."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "An error occurred while processing your request. Please try again later."
            )
        except Exception as e:
            logger.error(f"Error sending error message: {e}")

def main():
    """Start the bot."""
    # Start dummy server in a separate thread if on Render
    if os.getenv('RENDER'):
        port = int(os.getenv('PORT', 8080))
        server_thread = threading.Thread(target=run_dummy_server, args=(port,))
        server_thread.daemon = True
        server_thread.start()
        logger.info(f"Dummy server started on port {port}")

    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("authorize", authorize_user))
    application.add_handler(CommandHandler("unauthorize", unauthorize_user))
    
    # Track chats the bot is added to or removed from
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, track_chats))
    application.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, track_chats))
    
    # Handle links in messages
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_links))
    
    # Handle media messages for NSFW detection
    application.add_handler(MessageHandler(
        filters.PHOTO | filters.ANIMATION | filters.Sticker.ALL | filters.VIDEO | filters.Document.ALL,
        handle_media
    ))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Run the bot
    logger.info("Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
