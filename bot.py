import os
from pyrogram import Client, filters
from pyrogram.types import Message
from nudenet import NudeClassifier
from PIL import Image
import re
import cv2
import tempfile

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = 8122582244  # Replace with your Telegram User ID

# --- INIT ---
app = Client("bot", bot_token=BOT_TOKEN)
classifier = NudeClassifier()
AUTHORIZED_USERS = set()
AUTHORIZED_ADMINS = set()

# --- HELPERS ---

def contains_link(text):
    return bool(re.search(r'https?://|www\.', text))

def is_nsfw_image(file_path):
    result = classifier.classify(file_path)
    return result[file_path]['unsafe'] > 0.6

def extract_frames_from_video(file_path, max_frames=10):
    cap = cv2.VideoCapture(file_path)
    frames = []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, total // max_frames)

    for i in range(0, total, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if not ret:
            break
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        cv2.imwrite(tmp.name, frame)
        frames.append(tmp.name)
    cap.release()
    return frames

async def is_nsfw_video(file_path):
    frames = extract_frames_from_video(file_path)
    for f in frames:
        if is_nsfw_image(f):
            return True
    return False

# --- COMMANDS ---

@app.on_message(filters.command("approve") & filters.user(OWNER_ID))
async def approve(_, m: Message):
    if m.reply_to_message:
        uid = m.reply_to_message.from_user.id
        AUTHORIZED_USERS.add(uid)
        await m.reply(f"✅ Approved user {uid}")
    else:
        await m.reply("❗ Reply to a message to approve that user.")

@app.on_message(filters.command("approveadmin") & filters.user(OWNER_ID))
async def approveadmin(_, m: Message):
    if m.reply_to_message:
        uid = m.reply_to_message.from_user.id
        AUTHORIZED_ADMINS.add(uid)
        await m.reply(f"✅ Approved admin {uid}")
    else:
        await m.reply("❗ Reply to a message to approve that admin.")

# --- NSFW CHECK ---

@app.on_message(filters.group & (filters.photo | filters.document | filters.animation | filters.sticker))
async def nsfw_check(_, m: Message):
    try:
        path = await m.download()
        nsfw = False
        if m.photo or (m.document and m.document.mime_type.startswith("image/")):
            nsfw = is_nsfw_image(path)
        elif m.animation or m.sticker:
            nsfw = await is_nsfw_video(path)

        if nsfw:
            await m.delete()
            await m.reply("❌ NSFW content removed.")
    except Exception as e:
        print("NSFW detection error:", e)

# --- LINK BLOCKER ---

@app.on_message(filters.group & filters.text & ~filters.service)
async def link_blocker(_, m: Message):
    uid = m.from_user.id
    member = await app.get_chat_member(m.chat.id, uid)
    is_admin = member.status in ["administrator", "creator"]

    if contains_link(m.text):
        if uid == OWNER_ID:
            return
        if is_admin and uid not in AUTHORIZED_ADMINS:
            await m.delete()
        elif not is_admin and uid not in AUTHORIZED_USERS:
            await m.delete()

# --- START ---

@app.on_message(filters.command("start"))
async def start(_, m: Message):
    await m.reply("🤖 Bot is online and guarding this group!")

print("Bot running...")
app.run()
