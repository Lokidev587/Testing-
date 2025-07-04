import os import logging import tempfile from io import BytesIO from datetime import datetime, timedelta from http.server import BaseHTTPRequestHandler, HTTPServer import threading import re

from telegram import Update, ParseMode from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext from PIL import Image from nudenet import NudeDetector

Logging

logging.basicConfig( format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO ) logger = logging.getLogger(name)

Config

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") OWNER_ID = int(os.getenv("OWNER_ID", "8122582244"))

if not TELEGRAM_TOKEN: raise ValueError("TELEGRAM_TOKEN environment variable not set!")

nude_detector = NudeDetector()

Dummy server for Render

class HealthCheckServer(BaseHTTPRequestHandler): def do_GET(self): self.send_response(200) self.send_header('Content-type', 'text/plain') self.end_headers() self.wfile.write(b'Bot is alive!')

def run_health_server(): server = HTTPServer(('0.0.0.0', 8080), HealthCheckServer) logger.info("Health check server running on port 8080") server.serve_forever()

class GroupSecurityBot: def init(self): self.updater = Updater(TELEGRAM_TOKEN, use_context=True) self.dp = self.updater.dispatcher

self.authorized_users = [OWNER_ID]
    self.whitelisted_domains = ['telegram.org', 'wikipedia.org', 'github.com']
    self.promo_keywords = ['buy now', 'discount', 'promo', 'shop now']
    self.drug_keywords = ['weed', 'cocaine', 'heroin', 'drugs']
    self.weapon_keywords = ['gun', 'rifle', 'ammo', 'firearm']
    self.banned_stickers = set()

    self.setup_handlers()

def setup_handlers(self):
    self.dp.add_handler(CommandHandler("start", self.send_welcome))
    self.dp.add_handler(CommandHandler("authorize", self.authorize_user))
    self.dp.add_handler(CommandHandler("unauthorize", self.unauthorize_user))
    self.dp.add_handler(CommandHandler("ban_sticker", self.ban_sticker))

    self.dp.add_handler(MessageHandler(Filters.text | Filters.caption, self.handle_text))
    self.dp.add_handler(MessageHandler(Filters.photo | Filters.document, self.handle_media))
    self.dp.add_handler(MessageHandler(Filters.sticker, self.handle_sticker))

    self.dp.add_error_handler(self.error_handler)

def send_welcome(self, update: Update, context: CallbackContext):
    welcome_text = (
        "<b>🛡️ Group Protection Bot</b>\n\n"
        "Add me to your group as admin.\n\n"
        "I will automatically:\n"
        "- Delete unauthorized links\n"
        "- Remove NSFW media\n"
        "- Ban users who violate rules\n\n"
        "<b>Owner commands:</b>\n"
        "/authorize &lt;user_id&gt;\n"
        "/unauthorize &lt;user_id&gt;\n"
        "/ban_sticker (reply to sticker)"
    )
    update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)

def authorize_user(self, update: Update, context: CallbackContext):
    if update.effective_user.id != OWNER_ID:
        return
    try:
        user_id = int(context.args[0])
        self.authorized_users.append(user_id)
        update.message.reply_text(f"User {user_id} authorized.")
    except:
        update.message.reply_text("Usage: /authorize &lt;user_id&gt;")

def unauthorize_user(self, update: Update, context: CallbackContext):
    if update.effective_user.id != OWNER_ID:
        return
    try:
        user_id = int(context.args[0])
        self.authorized_users.remove(user_id)
        update.message.reply_text(f"User {user_id} unauthorized.")
    except:
        update.message.reply_text("Usage: /unauthorize &lt;user_id&gt;")

def ban_sticker(self, update: Update, context: CallbackContext):
    if update.effective_user.id != OWNER_ID:
        return
    if not update.message.reply_to_message or not update.message.reply_to_message.sticker:
        return
    sticker = update.message.reply_to_message.sticker
    self.banned_stickers.add(sticker.file_unique_id)
    update.message.reply_text("Sticker banned.")

def handle_text(self, update: Update, context: CallbackContext):
    message = update.message.text or update.message.caption or ""
    user = update.effective_user
    if self.contains_links(message) and user.id not in self.authorized_users:
        update.message.delete()
        self.warn_user(update, context, "Links are not allowed.")
    elif self.contains_bad_content(message):
        update.message.delete()
        self.ban_user(update, context, "Prohibited content")

def handle_media(self, update: Update, context: CallbackContext):
    user = update.effective_user
    if user.id in self.authorized_users:
        return
    file = update.message.photo[-1].get_file() if update.message.photo else update.message.document.get_file()
    file_bytes = BytesIO()
    file.download(out=file_bytes)
    file_bytes.seek(0)
    with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
        img = Image.open(file_bytes)
        img.save(tmp.name, format='JPEG')
        detections = nude_detector.detect(tmp.name)
        for d in detections:
            if d['class'] in ['EXPOSED_GENITALIA_M', 'EXPOSED_GENITALIA_F', 'EXPOSED_BREAST_F']:
                update.message.delete()
                self.ban_user(update, context, "NSFW content detected")
                return

def handle_sticker(self, update: Update, context: CallbackContext):
    sticker = update.message.sticker
    if sticker.file_unique_id in self.banned_stickers:
        update.message.delete()
        self.warn_user(update, context, "Banned sticker used.")
        return
    file = sticker.get_file()
    file_bytes = BytesIO()
    file.download(out=file_bytes)
    file_bytes.seek(0)
    with tempfile.NamedTemporaryFile(suffix=".webp") as tmp:
        img = Image.open(file_bytes)
        img.save(tmp.name, format='WEBP')
        detections = nude_detector.detect(tmp.name)
        for d in detections:
            if d['class'] in ['EXPOSED_GENITALIA_M', 'EXPOSED_GENITALIA_F', 'EXPOSED_BREAST_F']:
                update.message.delete()
                self.ban_user(update, context, "NSFW sticker used")
                self.banned_stickers.add(sticker.file_unique_id)
                return

def contains_links(self, text):
    urls = re.findall(r'https?://\S+', text.lower())
    return any(urls) and not any(domain in url for url in urls for domain in self.whitelisted_domains)

def contains_bad_content(self, text):
    text_lower = text.lower()
    return any(kw in text_lower for kw in self.promo_keywords + self.drug_keywords + self.weapon_keywords)

def warn_user(self, update: Update, context: CallbackContext, reason: str):
    try:
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"<b>⚠️ Warning:</b> <a href='tg://user?id={update.effective_user.id}'>User</a> - {reason}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to warn user: {e}")

def ban_user(self, update: Update, context: CallbackContext, reason: str):
    try:
        context.bot.ban_chat_member(
            chat_id=update.message.chat_id,
            user_id=update.effective_user.id,
            until_date=datetime.now() + timedelta(days=1))
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"<b>🚨 Banned:</b> <a href='tg://user?id={update.effective_user.id}'>User</a> - {reason}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ban error: {e}")

def error_handler(self, update: Update, context: CallbackContext):
    logger.error(f'Update "{update}" caused error "{context.error}"')

def run(self):
    threading.Thread(target=run_health_server, daemon=True).start()
    self.updater.start_polling(drop_pending_updates=True)
    logger.info("Bot is now running.")
    self.updater.idle()

if name == 'main': GroupSecurityBot().run()

