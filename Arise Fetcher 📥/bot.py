#!/usr/bin/env python3
# Arise Fetcher - Advanced Telegram Downloader Bot
# Version: 1.0.0
# Author: Arise Tech

import asyncio
import os
import re
import time
import random
import subprocess
import shutil
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultArticle, InputTextMessageContent
from pyrogram.enums import ParseMode
from dotenv import load_dotenv
import yt_dlp
import ffmpeg
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import firebase_admin
from firebase_admin import credentials, firestore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================== إعدادات التسجيل ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ================== تحميل المتغيرات البيئية ==================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []
FORCE_SUB_CHANNEL = os.getenv("FORCE_SUB_CHANNEL", "")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")

if not BOT_TOKEN or not API_ID or not API_HASH:
    raise ValueError("❌ تأكد من وجود BOT_TOKEN, API_ID, API_HASH في ملف .env")

# ================== تهيئة البوت ==================
app = Client("arise_fetcher", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
executor = ThreadPoolExecutor(max_workers=4)  # معالجة متوازية

# ================== تحديث yt-dlp التلقائي ==================
def update_ytdlp():
    try:
        result = subprocess.run(['yt-dlp', '--update-to', 'nightly'], capture_output=True, text=True, check=False)
        if "Updated yt-dlp" in result.stdout:
            logger.info("✅ yt-dlp تم تحديثه إلى الإصدار الليلي")
        return True
    except Exception as e:
        logger.error(f"⚠️ فشل تحديث yt-dlp: {e}")
        return False

update_ytdlp()

# ================== قواعد البيانات ==================
db = None
try:
    cred_firebase = credentials.Certificate("arise-tech-firebase.json")
    firebase_admin.initialize_app(cred_firebase)
    db = firestore.client()
    logger.info("✅ Firebase متصل")
except Exception as e:
    logger.warning(f"⚠️ Firebase غير متصل: {e}")

drive_service = None
if os.path.exists("credentials.json"):
    try:
        creds = Credentials.from_service_account_file("credentials.json")
        drive_service = build('drive', 'v3', credentials=creds)
        logger.info("✅ Google Drive جاهز")
    except Exception as e:
        logger.warning(f"⚠️ Google Drive فشل: {e}")

# ================== دوال الحماية والحظر ==================
PROXY_LIST = []  # ضع وكلاء حقيقيين هنا مثل ["http://user:pass@ip:port", ...]
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

def get_ydl_opts():
    return {
        'quiet': True,
        'no_warnings': True,
        'user_agent': random.choice(USER_AGENTS),
        'sleep_interval': random.randint(5, 15),
        'max_sleep_interval': 20,
        'sleep_interval_requests': 1,
        'extractor_retries': 3,
        'file_access_retries': 3,
        'retry_sleep_functions': {'http': 5, 'fragment': 5},
        'proxy': random.choice(PROXY_LIST) if PROXY_LIST else None,
    }

# ================== العلامة المائية الديناميكية ==================
def add_watermark_dynamic(input_path, output_path, text="Arise Fetcher"):
    """
    إضافة علامة مائية نصية بحجم يتناسب مع الفيديو (12% من الارتفاع)
    تعمل مع أي دقة فيديو
    """
    try:
        video = VideoFileClip(input_path)
        fontsize = max(20, int(video.h * 0.07))  # 7% من ارتفاع الفيديو، بحد أدنى 20
        txt_clip = (TextClip(text, fontsize=fontsize, color='white', font='Arial', stroke_color='black', stroke_width=1)
                    .set_opacity(0.75)
                    .set_duration(video.duration)
                    .set_pos(('right', 'bottom')))
        final = CompositeVideoClip([video, txt_clip])
        final.write_videofile(output_path, codec='libx264', audio_codec='aac', threads=4, fps=video.fps, logger=None)
        video.close()
        final.close()
        return True
    except Exception as e:
        logger.error(f"فشل إضافة العلامة المائية: {e}")
        # محاولة بديلة باستخدام ffmpeg
        return add_watermark_ffmpeg(input_path, output_path, text)

def add_watermark_ffmpeg(input_path, output_path, text="Arise Fetcher"):
    try:
        ffmpeg.input(input_path).output(
            output_path,
            vf=f"drawtext=text='{text}':fontcolor=white:fontsize=24:x=w-tw-10:y=10",
            **{'c:v': 'libx264', 'preset': 'fast', 'crf': 23}
        ).overwrite_output().run(quiet=True)
        return True
    except:
        return False

# ================== دوال معالجة الفيديو ==================
def compress_video(input_path, output_path, target_mb=48):
    try:
        probe = ffmpeg.probe(input_path)
        duration = float(probe['format']['duration'])
        size = os.path.getsize(input_path)
        if size <= target_mb * 1024 * 1024:
            shutil.copy(input_path, output_path)
            return True
        bitrate = int((target_mb * 1024 * 1024 * 8) / duration)
        bitrate = max(bitrate, 100000)  # حد أدنى 100k
        ffmpeg.input(input_path).output(output_path, **{'b:v': bitrate, 'c:v': 'libx264', 'preset': 'fast'}).overwrite_output().run(quiet=True)
        return True
    except Exception as e:
        logger.error(f"فشل الضغط: {e}")
        return False

def upload_to_gdrive(file_path, file_name):
    if not drive_service:
        return None
    try:
        metadata = {'name': file_name}
        if GOOGLE_DRIVE_FOLDER_ID:
            metadata['parents'] = [GOOGLE_DRIVE_FOLDER_ID]
        media = MediaFileUpload(file_path, resumable=True)
        file = drive_service.files().create(body=metadata, media_body=media, fields='id').execute()
        file_id = file.get('id')
        drive_service.permissions().create(fileId=file_id, body={'type': 'anyone', 'role': 'reader'}).execute()
        return f"https://drive.google.com/file/d/{file_id}/view"
    except Exception as e:
        logger.error(f"فشل الرفع لجوجل درايف: {e}")
        return None

def clean_old_files(max_age_hours=1):
    """حذف الملفات الأقدم من ساعة لتنظيف المساحة"""
    now = time.time()
    for f in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, f)
        if os.path.isfile(path) and (now - os.path.getmtime(path)) > max_age_hours * 3600:
            os.remove(path)
            logger.info(f"تم حذف ملف قديم: {f}")

# ================== دوال التحميل الرئيسية ==================
def download_media(url, media_type, quality, user_id):
    """دالة متزامنة للتحميل والمعالجة (تُشغل في thread منفصل)"""
    try:
        # جلب عنوان الفيديو
        title = get_video_title(url)
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:100]
        timestamp = int(time.time())
        base_filename = f"{safe_title}_{user_id}_{timestamp}"

        if media_type == "video":
            fmt = {
                "best": "best[height<=1080]",
                "medium": "best[height<=480]",
                "worst": "worst[height<=360]"
            }.get(quality, "best[height<=480]")

            temp_output = os.path.join(DOWNLOAD_DIR, f"{base_filename}_temp.mp4")
            opts = get_ydl_opts()
            opts['format'] = fmt
            opts['outtmpl'] = temp_output

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            # ضغط إذا لزم الأمر
            if os.path.getsize(temp_output) > 48 * 1024 * 1024:
                compressed = os.path.join(DOWNLOAD_DIR, f"{base_filename}_compressed.mp4")
                if compress_video(temp_output, compressed):
                    os.remove(temp_output)
                    temp_output = compressed

            # إضافة علامة مائية
            watermarked = os.path.join(DOWNLOAD_DIR, f"{base_filename}_wm.mp4")
            if add_watermark_dynamic(temp_output, watermarked):
                os.remove(temp_output)
                final_path = watermarked
            else:
                final_path = temp_output

            # إذا كان لا يزال أكبر من 48 ميجا، نرفعه لجوجل درايف
            if os.path.getsize(final_path) > 48 * 1024 * 1024:
                drive_link = upload_to_gdrive(final_path, f"{safe_title}.mp4")
                os.remove(final_path)
                if drive_link:
                    return {"success": True, "drive_link": drive_link, "title": title}
                else:
                    return {"success": False, "error": "الملف كبير جداً وفشل رفعه لجوجل درايف"}

            return {"success": True, "file_path": final_path, "title": title}

        else:  # audio
            bitrate = "192" if quality == "192" else "128"
            temp_output = os.path.join(DOWNLOAD_DIR, f"{base_filename}.%(ext)s")
            opts = get_ydl_opts()
            opts['format'] = 'bestaudio/best'
            opts['outtmpl'] = temp_output
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': bitrate,
            }]

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            # البحث عن الملف النهائي
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(base_filename) and f.endswith('.mp3'):
                    final_path = os.path.join(DOWNLOAD_DIR, f)
                    return {"success": True, "file_path": final_path, "title": title}

            return {"success": False, "error": "لم يتم العثور على الملف الصوتي"}

    except Exception as e:
        logger.error(f"فشل التحميل للمستخدم {user_id}: {e}")
        return {"success": False, "error": str(e)}

def get_video_title(url):
    try:
        opts = {'quiet': True, 'extract_flat': True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get('title', 'Arise_Video')
    except:
        return "Arise_Video"

# ================== أوامر البوت (غير متزامنة) ==================
user_data = {}  # تخزين مؤقت

@app.on_message(filters.command("start"))
async def start_command(client, message):
    text = (
        "🚀 **Arise Fetcher** – بوت التحميل المتطور\n\n"
        "اختر المنصة التي تريد التحميل منها:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📺 YouTube", callback_data="platform_youtube"),
         InlineKeyboardButton("📸 Instagram", callback_data="platform_instagram")],
        [InlineKeyboardButton("🎵 TikTok", callback_data="platform_tiktok"),
         InlineKeyboardButton("🐦 Twitter", callback_data="platform_twitter")],
        [InlineKeyboardButton("💰 اشتراك مميز", callback_data="subscribe"),
         InlineKeyboardButton("❓ مساعدة", callback_data="help")]
    ])
    await message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

@app.on_callback_query(filters.regex("platform_"))
async def platform_callback(client, callback_query):
    platform = callback_query.data.split("_")[1]
    user_id = callback_query.from_user.id
    user_data[user_id] = {"platform": platform}
    await callback_query.message.edit_text(f"✅ تم اختيار {platform.capitalize()}\nأرسل رابط الفيديو:")
    await callback_query.answer()

@app.on_message(filters.text & filters.private & ~filters.command(["start", "help", "subscribe"]))
async def handle_url(client, message):
    user_id = message.from_user.id
    url = message.text.strip()
    if user_id not in user_data or not url.startswith("http"):
        await message.reply_text("❌ الرابط غير صالح. استخدم /start ثم اختر المنصة.")
        return
    user_data[user_id]["url"] = url
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 فيديو", callback_data="type_video"),
         InlineKeyboardButton("🎵 صوت", callback_data="type_audio")]
    ])
    await message.reply_text("ماذا تريد تحميل؟", reply_markup=keyboard)

@app.on_callback_query(filters.regex("type_"))
async def type_callback(client, callback_query):
    user_id = callback_query.from_user.id
    media_type = callback_query.data.split("_")[1]
    if user_id not in user_data:
        await callback_query.answer("انتهت الجلسة، استخدم /start", show_alert=True)
        return
    user_data[user_id]["media_type"] = media_type

    if media_type == "video":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 عالية (1080p)", callback_data="quality_best"),
             InlineKeyboardButton("📱 متوسطة (480p)", callback_data="quality_medium")],
            [InlineKeyboardButton("📀 منخفضة (360p)", callback_data="quality_worst")]
        ])
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎧 عالية (192kbps)", callback_data="quality_192"),
             InlineKeyboardButton("📻 متوسطة (128kbps)", callback_data="quality_128")]
        ])
    await callback_query.message.edit_text("اختر الجودة:", reply_markup=keyboard)
    await callback_query.answer()

@app.on_callback_query(filters.regex("quality_"))
async def download_callback(client, callback_query):
    user_id = callback_query.from_user.id
    quality = callback_query.data.split("_")[1]
    if user_id not in user_data:
        await callback_query.answer("انتهت الجلسة", show_alert=True)
        return

    data = user_data[user_id]
    url = data["url"]
    media_type = data["media_type"]

    # رسالة التحميل
    status_msg = await callback_query.message.edit_text(
        "⏳ **جاري التحميل والمعالجة...**\n"
        "🚀 Arise Fetcher – هذا قد يستغرق دقيقة حسب حجم الفيديو",
        parse_mode=ParseMode.MARKDOWN
    )

    # تشغيل التحميل في thread منفصل
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, download_media, url, media_type, quality, user_id)

    if result["success"]:
        if "drive_link" in result:
            await client.send_message(
                user_id,
                f"✅ **{result['title']}**\n"
                f"📁 الملف كبير جداً لتليجرام، تم رفعه لجوجل درايف:\n{result['drive_link']}\n"
                f"🏷️ Arise Fetcher"
            )
        else:
            # إرسال الملف
            file_path = result["file_path"]
            caption = f"✅ **{result['title']}**\n🏷️ Arise Fetcher"
            if media_type == "video":
                await client.send_video(user_id, file_path, caption=caption, parse_mode=ParseMode.MARKDOWN)
            else:
                await client.send_audio(user_id, file_path, caption=caption, parse_mode=ParseMode.MARKDOWN)
            os.remove(file_path)  # حذف فوري بعد الإرسال
    else:
        await client.send_message(user_id, f"❌ فشل التحميل:\n{result['error']}")

    await status_msg.delete()
    del user_data[user_id]
    await callback_query.answer()

# ================== الاشتراك والدعم ==================
@app.on_callback_query(filters.regex("subscribe"))
async def subscribe_callback(client, callback_query):
    text = (
        "🌟 **Arise Fetcher Premium** 🌟\n\n"
        "✅ تحميل غير محدود\n"
        "✅ جودة 4K و 1080p 60fps\n"
        "✅ تحميل قوائم التشغيل\n"
        "✅ أولوية معالجة سريعة\n"
        "✅ دعم VIP 24/7\n\n"
        "للاشتراك، تواصل مع @AriseTechSupport"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
    ])
    await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    await callback_query.answer()

@app.on_callback_query(filters.regex("help"))
async def help_callback(client, callback_query):
    text = (
        "🔹 **كيفية استخدام Arise Fetcher**\n\n"
        "1. اضغط /start\n"
        "2. اختر المنصة (يوتيوب، إنستا، تيك توك، تويتر)\n"
        "3. أرسل رابط الفيديو\n"
        "4. اختر فيديو أو صوت\n"
        "5. اختر الجودة المناسبة\n"
        "6. انتظر قليلاً – سيتم إرسال الملف مع علامة Arise Fetcher\n\n"
        "📌 **ملاحظات:**\n"
        "• الحد الأقصى للحجم 50 ميجابايت – إذا كان أكبر، نرفعه لجوجل درايف\n"
        "• العلامة المائية تضاف تلقائياً للحفاظ على حقوق Arise Tech\n"
        "• للاشتراك المميز، اضغط على الزر المخصص"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
    ])
    await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    await callback_query.answer()

@app.on_callback_query(filters.regex("back"))
async def back_callback(client, callback_query):
    await start_command(client, callback_query.message)

# ================== أوامر المسؤول ==================
@app.on_message(filters.command("stats") & filters.user(ADMIN_IDS))
async def admin_stats(client, message):
    total_files = len(os.listdir(DOWNLOAD_DIR))
    disk_usage = shutil.disk_usage(DOWNLOAD_DIR)
    text = (
        f"📊 **إحصائيات Arise Fetcher**\n\n"
        f"📁 ملفات مؤقتة: {total_files}\n"
        f"💾 مساحة مستخدمة: {disk_usage.used // (1024**2)} MB\n"
        f"🔄 yt-dlp: محدث\n"
        f"🚀 الحالة: يعمل بكفاءة"
    )
    await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

@app.on_message(filters.command("clean") & filters.user(ADMIN_IDS))
async def admin_clean(client, message):
    clean_old_files(0)  # حذف كل الملفات
    await message.reply_text("✅ تم تنظيف مجلد التحميلات")

# ================== مهمة تنظيف مجدولة ==================
scheduler = AsyncIOScheduler()
scheduler.add_job(clean_old_files, 'interval', hours=1, args=[1])
scheduler.start()

# ================== تشغيل البوت ==================
if __name__ == "__main__":
    logger.info("🚀 Arise Fetcher يعمل الآن...")
    logger.info(f"👥 المسؤولون: {ADMIN_IDS}")
    app.run()