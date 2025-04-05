import logging
import asyncpg
import json
import os
import asyncio  # Bu qator qo'shildi
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv
from functools import wraps

# Logging sozlamalari
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# .env fayldan o'zgaruvchilarni yuklash
load_dotenv()
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")

# Global ma'lumotlar
message_store = {}
users = {}
channels = []

# Connection Pool
db_pool = None


# Qolgan kod o'zgarishsiz qoladi...

async def init_db_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(
        database=DB_NAME, user=DB_USER,
        password=DB_PASSWORD, host=DB_HOST, port=DB_PORT,
        min_size=1, max_size=10
    )


# Dekorator: Ma'lumotlar bazasi ulanishini boshqarish
def with_db_connection(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        async with db_pool.acquire() as conn:
            return await func(conn, *args, **kwargs)

    return wrapper


# Ma'lumotlarni yuklash
@with_db_connection
async def load_message_store(conn):
    global message_store
    rows = await conn.fetch("SELECT * FROM message_store")
    message_store.update({
        row["movie_code"]: {
            "message_id": row["message_id"],
            "video": row["video"],
            "document": row["document"],
            "caption": row["caption"],
            "name": row["name"],
            "views": row["views"]
        } for row in rows
    })


@with_db_connection
async def load_users(conn):
    global users
    rows = await conn.fetch("SELECT user_id, data FROM users")
    users.update({row["user_id"]: row["data"] for row in rows})


@with_db_connection
async def load_channels(conn):
    global channels
    rows = await conn.fetch("SELECT channel_username FROM channels")
    channels[:] = [f"https://t.me/{row['channel_username']}" for row in rows]


# Ma'lumotlarni saqlash
@with_db_connection
async def save_message_store(conn, movie_code, data):
    await conn.execute("""
        INSERT INTO message_store (movie_code, message_id, video, document, caption, name, views)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (movie_code) DO UPDATE 
        SET message_id = EXCLUDED.message_id,
            video = EXCLUDED.video,
            document = EXCLUDED.document,
            caption = EXCLUDED.caption,
            name = EXCLUDED.name,
            views = EXCLUDED.views
    """, movie_code, data["message_id"], data["video"], data["document"],
                       data["caption"], data["name"], data["views"])


@with_db_connection
async def save_user(conn, user_id: int, user_data: dict):
    await conn.execute("""
        INSERT INTO users (user_id, data) 
        VALUES ($1, $2)
        ON CONFLICT (user_id) 
        DO UPDATE SET data = EXCLUDED.data
    """, user_id, user_data)


# Admin tekshiruvi dekoratori
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.message.from_user.id != ADMIN_ID:
            await update.message.reply_text("Sizda bu buyruqni ishlatish huquqi yo'q.")
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


# Kanal qo'shish/o'chirish
@admin_only
@with_db_connection
async def add_channel(conn, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Iltimos, kanal URL'sini kiriting. Masalan: /addchannel https://t.me/kanal_nomi")
        return
    channel_url = context.args[0]
    if not channel_url.startswith("https://t.me/"):
        await update.message.reply_text("URL `https://t.me/` bilan boshlanishi kerak.")
        return
    channel_username = channel_url.split("/")[-1]
    if f"https://t.me/{channel_username}" in channels:
        await update.message.reply_text("Kanal allaqachon ro'yxatda.")
    else:
        await conn.execute("INSERT INTO channels (channel_username) VALUES ($1)", channel_username)
        channels.append(channel_url)
        await update.message.reply_text(f"Kanal qo'shildi: {channel_url}")


@admin_only
@with_db_connection
async def remove_channel(conn, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Iltimos, kanal URL'sini kiriting. Masalan: /removechannel https://t.me/kanal_nomi")
        return
    channel_url = context.args[0]
    if not channel_url.startswith("https://t.me/"):
        await update.message.reply_text("URL `https://t.me/` bilan boshlanishi kerak.")
        return
    channel_username = channel_url.split("/")[-1]
    result = await conn.execute("DELETE FROM channels WHERE channel_username = $1", channel_username)
    if result == "DELETE 1":
        channels.remove(channel_url)
        await update.message.reply_text(f"Kanal olib tashlandi: {channel_url}")
    else:
        await update.message.reply_text("Bu kanal ro'yxatda mavjud emas.")


# Obuna tekshiruvi
async def check_subscription(user_id, bot):
    for channel in channels:
        try:
            chat_member = await bot.get_chat_member(chat_id=f"@{channel.split('/')[-1]}", user_id=user_id)
            if chat_member.status not in ['member', 'administrator', 'creator']:
                return False
        except Exception as e:
            logger.warning(f"Kanal {channel} tekshirilganda xatolik: {e}")
            return False
    return True


async def send_subscription_prompt(chat_id, context):
    keyboard = [
        [InlineKeyboardButton(f"Kanal {i + 1}", url=channel) for i, channel in enumerate(channels)],
        [InlineKeyboardButton("Obunani tekshirish", callback_data='check_subscription')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=chat_id, text="Botdan foydalanish uchun kanallarga obuna bo'ling:",
                                   reply_markup=reply_markup)


# Asosiy funksiyalar
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    username = update.message.from_user.username

    if user_id not in users:
        user_data = json.dumps({"username": username, "first_name": user_name})
        await save_user(user_id, user_data)
        users[user_id] = user_data

    if await check_subscription(user_id, context.bot):
        await update.message.reply_text(f"Xush kelibsiz, {user_name}! Film kodini yuboring.")
    else:
        await send_subscription_prompt(update.effective_chat.id, context)


async def find_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_code = update.message.text.strip()
    user_id = update.message.from_user.id

    if not await check_subscription(user_id, context.bot):
        await send_subscription_prompt(update.effective_chat.id, context)
        return

    if user_code in message_store:
        movie = message_store[user_code]
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE message_store SET views = views + 1 WHERE movie_code = $1", user_code)
        message_store[user_code]["views"] += 1
        if movie["video"]:
            await context.bot.send_video(chat_id=update.effective_chat.id, video=movie["video"],
                                         caption=movie["caption"])
        elif movie["document"]:
            await context.bot.send_document(chat_id=update.effective_chat.id, document=movie["document"],
                                            caption=movie["caption"])
    else:
        await update.message.reply_text("Afsuski, bunday film mavjud emas.")


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post.caption:
        return
    caption = update.channel_post.caption.strip()
    movie_name = "Nom mavjud emas"
    if 'Nomi:' in caption:
        try:
            name_start = caption.index('Nomi:') + len('Nomi:') + 1
            name_end = caption.index('"', name_start)
            movie_name = caption[name_start:name_end].strip()
        except ValueError:
            logger.warning("Kino nomini ajratib bo'lmadi.")
    if '<' in caption and '>' in caption:
        code_start = caption.index('<') + 1
        code_end = caption.index('>')
        movie_code = caption[code_start:code_end].strip()
        data = {
            "message_id": update.channel_post.message_id,
            "video": update.channel_post.video.file_id if update.channel_post.video else None,
            "document": update.channel_post.document.file_id if update.channel_post.document else None,
            "caption": caption,
            "name": movie_name,
            "views": 0
        }
        await save_message_store(movie_code, data)
        message_store[movie_code] = data


@admin_only
@with_db_connection
async def top_movies(conn, update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await conn.fetch("SELECT name, movie_code, views FROM message_store ORDER BY views DESC LIMIT 10")
    if not rows:
        await update.message.reply_text("Hozircha film tomosha qilinmagan.")
        return
    top_list = "\n".join([f"{i + 1}. {row['name']} - {row['movie_code']} ({row['views']} marta ko‘rilgan)" for i, row in
                          enumerate(rows)])
    await update.message.reply_text(f"Eng ko‘p tomosha qilingan filmlar:\n{top_list}")


@admin_only
@with_db_connection
async def list_users(conn, update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await conn.fetch("SELECT user_id, data FROM users")
    if not rows:
        await update.message.reply_text("Foydalanuvchilar mavjud emas.")
        return
    user_list = "\n".join([
        f"ID: {row['user_id']}, Username: {'@' + json.loads(row['data'])['username'] if json.loads(row['data'])['username'] else 'None'}, Name: {json.loads(row['data'])['first_name']}"
        for row in rows])
    await update.message.reply_text(f"Barcha foydalanuvchilar:\n{user_list}")


@admin_only
async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if channels:
        channel_list = "\n".join([channel.split('/')[-1] for channel in channels])
        await update.message.reply_text(f"Mavjud kanallar:\n{channel_list}")
    else:
        await update.message.reply_text("Hozircha kanal mavjud emas.")


@admin_only
async def admin_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    commands_list = (
        "/start - Botni boshlash\n"
        "/stat - Botning statistikasi\n"
        "/top - Eng ko'p tomosha qilingan filmlar\n"
        "/users - Foydalanuvchilar ro'yxati\n"
        "/addchannel - Kanal qo'shish\n"
        "/removechannel - Kanal olib tashlash\n"
        "/channels - Mavjud kanallar\n"
        "/broadcast - Xabar yuborish\n"
        "/admin - Admin komandalari"
    )
    await update.message.reply_text(f"Admin komandalari:\n{commands_list}")


@admin_only
@with_db_connection
async def broadcast(conn, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Iltimos, xabar yoki media orqali javob qiling.")
        return
    reply_message = update.message.reply_to_message
    rows = await conn.fetch("SELECT user_id FROM users")
    user_ids = [row["user_id"] for row in rows]
    failed_users = []
    for user_id in user_ids:
        try:
            if reply_message.text:
                await context.bot.send_message(chat_id=user_id, text=reply_message.text)
            elif reply_message.photo:
                await context.bot.send_photo(chat_id=user_id, photo=reply_message.photo[-1].file_id,
                                             caption=reply_message.caption)
            elif reply_message.video:
                await context.bot.send_video(chat_id=user_id, video=reply_message.video.file_id,
                                             caption=reply_message.caption)
            elif reply_message.document:
                await context.bot.send_document(chat_id=user_id, document=reply_message.document.file_id,
                                                caption=reply_message.caption)
            elif reply_message.animation:
                await context.bot.send_animation(chat_id=user_id, animation=reply_message.animation.file_id,
                                                 caption=reply_message.caption)
        except Exception as e:
            logger.warning(f"Xabar yuborishda xato {user_id}: {e}")
            failed_users.append(user_id)
    await update.message.reply_text(f"Xabar yuborildi. Muvaffaqiyatsiz: {len(failed_users)}")


@with_db_connection
async def stat(conn, update: Update, context: ContextTypes.DEFAULT_TYPE):
    users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
    movies_count = await conn.fetchval("SELECT COUNT(*) FROM message_store")
    await update.message.reply_text(
        f"📊 Statistika:\n👤 Foydalanuvchilar: {users_count}\n🎬 Kinolar: {movies_count}\n📌 Kanal: @movies_reel")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await check_subscription(query.from_user.id, context.bot):
        await query.edit_message_text("Kanallarga obuna bo'lgansiz! Film kodini yuboring.")
    else:
        await query.edit_message_text("Iltimos, avval kanallarga obuna bo‘ling!")


async def main():
    # Ma'lumotlar bazasi poolini ishga tushirish
    await init_db_pool()
    # Ma'lumotlarni yuklash
    await load_message_store()
    await load_users()
    await load_channels()

    # Botni yaratish va handlerlarni qo'shish
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stat", stat))
    app.add_handler(CommandHandler("top", top_movies))
    app.add_handler(CommandHandler("users", list_users))
    app.add_handler(CommandHandler("addchannel", add_channel))
    app.add_handler(CommandHandler("removechannel", remove_channel))
    app.add_handler(CommandHandler("channels", channels_cmd))
    app.add_handler(CommandHandler("admin", admin_commands))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, find_movie))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))
    app.add_handler(CallbackQueryHandler(button_callback))

    # Pollingni boshlash
    logger.info("Bot ishga tushdi...")
    await app.initialize()  # Botni tayyorlash
    await app.start()  # Botni ishga tushirish
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)  # Pollingni boshlash

    # Botni doimiy ishlashda ushlab turish
    try:
        await asyncio.Event().wait()  # Cheksiz kutish
    except KeyboardInterrupt:
        logger.info("Bot to'xtatilmoqda...")
    finally:
        await app.updater.stop()  # Pollingni to'xtatish
        await app.stop()  # Botni to'xtatish
        await app.shutdown()  # Resurslarni tozalash


if __name__ == "__main__":
    asyncio.run(main())
