import logging
import asyncio
import os

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from nudenet import NudeDetector

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
NSFW_CLASSES = {
    "FEMALE_GENITALIA_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "ANUS_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "BELLY_EXPOSED",
    "ARMPITS_EXPOSED"
}
# ============================================

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load NudeDetector once
logger.info("Loading NudeDetector...")
detector = NudeDetector()
logger.info("✅ NudeDetector ready.")

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot is running and monitoring media for NSFW content.")

# Check media content for nudity
async def nsfw_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    file = None
    try:
        if update.message.photo:
            file = await update.message.photo[-1].get_file()
        elif update.message.video:
            file = await update.message.video.get_file()
        elif update.message.animation:
            file = await update.message.animation.get_file()
        elif update.message.sticker and update.message.sticker.is_video:
            file = await update.message.sticker.get_file()
        elif update.message.document and update.message.document.mime_type.startswith("image/"):
            file = await update.message.document.get_file()
        else:
            return  # Not a media we check

        file_path = await file.download_to_drive()
        detections = detector.detect(file_path)

        # Check if NSFW class is found
        for detection in detections:
            if detection["class"] in NSFW_CLASSES:
                await update.message.delete()
                logger.info(f"❌ Deleted NSFW content sent by {update.effective_user.id}")
                break

        os.remove(file_path)  # Clean up

    except Exception as e:
        logger.error(f"Error in nsfw_filter: {e}")

# Delete links from any user or admin
async def link_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and ("http://" in update.message.text or "https://" in update.message.text):
        try:
            await update.message.delete()
            logger.info(f"🔗 Deleted link from {update.effective_user.id}")
        except Exception as e:
            logger.error(f"Couldn't delete message: {e}")

# Run bot
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # Media message handler
    app.add_handler(MessageHandler(
        filters.PHOTO |
        filters.VIDEO |
        filters.ANIMATION |
        filters.Document.IMAGE |
        filters.Sticker.VIDEO,
        nsfw_filter
    ))

    # Link deletion
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"http[s]?://"), link_filter))

    logger.info("🤖 Bot started polling.")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
