import logging
import asyncpg
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest
from dotenv import load_dotenv

# Bot Token from BotFather
load_dotenv()

# O'zgaruvchilarni olish
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")


# PostgreSQL bilan bog'lanish uchun funksiya
async def connect_db():
    return await asyncpg.connect(
        database=DB_NAME, user=DB_USER,
        password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
    )


logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)


# Barcha saqlangan kinolarni yuklash
async def load_message_store():
    conn = await connect_db()
    rows = await conn.fetch("SELECT * FROM message_store")
    message_store = {
        row["movie_code"]: {
            "message_id": row["message_id"],
            "video": row["video"],
            "document": row["document"],
            "caption": row["caption"],
            "name": row["name"],
            "views": row["views"]
        }
        for row in rows
    }
    await conn.close()
    return message_store


async def save_message_store(message_store):
    conn = await connect_db()
    for movie_code, data in message_store.items():
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

    await conn.close()


async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kanal qo'shish funksiyasi"""
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("Sizda kanal qo'shish huquqi yo'q.")
        return

    if not context.args:
        await update.message.reply_text(
            "Iltimos, kanal URL'sini kiriting. Masalan: /addchannel https://t.me/kanal_nomi")
        return

    channel_url = context.args[0]

    if not channel_url.startswith("https://t.me/"):
        await update.message.reply_text(
            "Iltimos, kanal URL'sini `https://t.me/` bilan kiriting. Masalan: /addchannel https://t.me/kanal_nomi")
        return

    channel_username = channel_url.split("/")[-1]  # URL'dan username ajratib olish

    conn = await connect_db()
    if not conn:
        await update.message.reply_text("Baza bilan bog'lanishda muammo yuz berdi.")
        return

    existing_channel = await conn.fetchval(
        "SELECT channel_username FROM channels WHERE channel_username = $1", channel_username
    )

    if existing_channel:
        await update.message.reply_text("Kanal allaqachon ro'yxatda mavjud.")
    else:
        await conn.execute("INSERT INTO channels (channel_username) VALUES ($1)", channel_username)
        await update.message.reply_text(f"Kanal qo'shildi: {channel_url}")

    await conn.close()


async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kanalni o‘chirish funksiyasi"""
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("Sizda kanal olib tashlash huquqi yo'q.")
        return

    if not context.args:
        await update.message.reply_text(
            "Iltimos, o‘chirish uchun kanal URL'sini kiriting. Masalan: /removechannel https://t.me/kanal_nomi")
        return

    channel_url = context.args[0]

    if not channel_url.startswith("https://t.me/"):
        await update.message.reply_text(
            "Iltimos, kanal URL'sini `https://t.me/` bilan kiriting. Masalan: /removechannel https://t.me/kanal_nomi")
        return

    channel_username = channel_url.split("/")[-1]  # URL'dan username ajratib olish

    conn = await connect_db()
    if not conn:
        await update.message.reply_text("Baza bilan bog'lanishda muammo yuz berdi.")
        return

    result = await conn.execute("DELETE FROM channels WHERE channel_username = $1", channel_username)
    await conn.close()

    if result == "DELETE 1":
        await update.message.reply_text(f"Kanal olib tashlandi: {channel_url}")
    else:
        await update.message.reply_text("Bu kanal ro‘yxatda mavjud emas.")


# Barcha kanallarni yuklash
async def load_channels():
    conn = await connect_db()
    rows = await conn.fetch("SELECT channel_username FROM channels")
    channels = [f"https://t.me/{row['channel_username']}" for row in rows]  # URL sifatida qaytarish
    await conn.close()
    return channels


# Check if the user is subscribed to required channels
async def get_channels():
    conn = await connect_db()
    rows = await conn.fetch("SELECT channel_username FROM channels")
    await conn.close()
    return [row['channel_username'] for row in rows]


async def check_subscription(user_id, bot):
    """Foydalanuvchini barcha kanallarga a’zo ekanligini tekshiradi"""
    channels = await get_channels()  # Bazadan kanallarni olish

    for channel in channels:
        try:
            chat_member = await bot.get_chat_member(chat_id=f"@{channel}", user_id=user_id)
            if chat_member.status not in ['member', 'administrator', 'creator']:
                return False
        except Exception as e:
            logging.warning(f"Kanal {channel} tekshirilganda xatolik: {e}")
            return False
    return True


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    is_subscribed = await check_subscription(user_id, context.bot)  # PostgreSQL-dan tekshirish

    if is_subscribed:
        await query.edit_message_text("Siz kanallarga obuna bo'lgansiz! Marhamat, menga film kodini yuboring.")
    else:
        await query.edit_message_text("Iltimos, botdan foydalanish uchun avval barcha kanallarga obuna bo‘ling!")


async def load_users():
    conn = await connect_db()
    rows = await conn.fetch("SELECT user_id, data FROM users")

    users = {row["user_id"]: row["data"] for row in rows}  # JSONB ni dictionary shaklida saqlaymiz

    await conn.close()
    return users  # Foydalanuvchilarni dictionary sifatida qaytaramiz


# Foydalanuvchini qo'shish yoki yangilash
async def save_user(user_id: int, user_data: dict):
    conn = await connect_db()
    await conn.execute(
        """
        INSERT INTO users (user_id, data) 
        VALUES ($1, $2)
        ON CONFLICT (user_id) 
        DO UPDATE SET data = EXCLUDED.data
        """,
        user_id, user_data
    )
    await conn.close()


async def send_subscription_prompt(chat_id, context):
    channels = await get_channels()  # PostgreSQL'dan kanallarni olish

    keyboard = [
        [InlineKeyboardButton(f"Kanal {i + 1}", url=f"https://t.me/{channel}") for i, channel in enumerate(channels)],
        [InlineKeyboardButton("Obunani tekshirish", callback_data='check_subscription')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=chat_id,
        text="Botdan foydalanish uchun quyidagi kanallarga obuna bo'lishingiz kerak:",
        reply_markup=reply_markup
    )


async def find_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_code = update.message.text.strip()
    user_id = update.message.from_user.id

    # Obunani tekshirish
    is_subscribed = await check_subscription(user_id, context.bot)
    if not is_subscribed:
        await send_subscription_prompt(update.effective_chat.id, context)
        return

    # PostgreSQL bazasiga ulanish
    conn = await connect_db()

    # Kino ma'lumotlarini olish
    movie = await conn.fetchrow("SELECT * FROM message_store WHERE movie_code = $1", user_code)

    if movie:
        # Ko‘rishlar sonini oshirish
        await conn.execute("UPDATE message_store SET views = views + 1 WHERE movie_code = $1", user_code)
        await conn.close()

        # Videoni yoki hujjatni yuborish
        if movie["video"]:
            await context.bot.send_video(chat_id=update.effective_chat.id, video=movie["video"],
                                         caption=movie["caption"])
        elif movie["document"]:
            await context.bot.send_document(chat_id=update.effective_chat.id, document=movie["document"],
                                            caption=movie["caption"])
        return

    await conn.close()
    await update.message.reply_text("Afsuski, bunday film mavjud emas.")


# start funksiyasi: Xush kelibsiz xabari va obuna tekshiruvi
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.from_user.first_name
    user_id = update.message.from_user.id
    username = update.message.from_user.username

    conn = await connect_db()

    # Foydalanuvchi bazada bor-yo‘qligini tekshirish
    user_exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM users WHERE user_id = $1)", user_id)

    if not user_exists:
        # Yangi foydalanuvchini qo‘shish
        await conn.execute(
            "INSERT INTO users (user_id, data) VALUES ($1, $2)",
            user_id,
            json.dumps({"username": username, "first_name": user_name})  # `dict`ni JSON formatga o‘tkazish
        )

    # Obunani tekshirish
    is_subscribed = await check_subscription(user_id, context.bot)
    if is_subscribed:
        await update.message.reply_text(f"Xush kelibsiz, {user_name}! Marhamat, menga film kodini yuboring.")
    else:
        await send_subscription_prompt(update.effective_chat.id, context)

    await conn.close()


# Kanal postini qayta ishlash va bazaga saqlash
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("handle_channel_post chaqirildi!")  # ✅ Funksiya ishlayotganini tekshiramiz
    print("handle_channel_post chaqirildi!")
    if update.channel_post.caption:
        caption = update.channel_post.caption.strip()
        movie_name = "Nom mavjud emas"

        if 'Nomi:' in caption:
            try:
                name_start = caption.index('Nomi:') + len('Nomi:')
                name_start = caption.index('"', name_start) + 1
                name_end = caption.index('"', name_start)
                movie_name = caption[name_start:name_end].strip()
            except ValueError:
                logging.warning("Failed to extract movie name from caption.")

        if '<' in caption and '>' in caption:
            code_start = caption.index('<') + 1
            code_end = caption.index('>')
            movie_code = caption[code_start:code_end].strip()

            message_id = update.channel_post.message_id
            video = update.channel_post.video.file_id if update.channel_post.video else None
            document = update.channel_post.document.file_id if update.channel_post.document else None

            # PostgreSQL bazaga yozish
            conn = await connect_db()
            await conn.execute("""
                INSERT INTO message_store (movie_code, message_id, video, document, caption, name, views)
                VALUES ($1, $2, $3, $4, $5, $6, 0)
                ON CONFLICT (movie_code) DO UPDATE 
                SET message_id = EXCLUDED.message_id,
                    video = EXCLUDED.video,
                    document = EXCLUDED.document,
                    caption = EXCLUDED.caption,
                    name = EXCLUDED.name
            """, movie_code, message_id, video, document, caption, movie_name)

            await conn.close()

            logging.info(f"Saqlangan kino: {movie_name} (Kod: {movie_code})")


async def top_movies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = await connect_db()
    rows = await conn.fetch("SELECT name, movie_code, views FROM message_store ORDER BY views DESC LIMIT 10")
    await conn.close()

    if not rows:
        await update.message.reply_text("Hozircha hech qanday film tomosha qilinmagan.")
        return

    top_list = "\n".join([f"{i + 1}. {row['name']} - {row['movie_code']} ({row['views']} marta ko‘rilgan)"
                          for i, row in enumerate(rows)])

    await update.message.reply_text(f"Eng ko‘p tomosha qilingan filmlar:\n{top_list}")


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id == ADMIN_ID:
        conn = await connect_db()
        rows = await conn.fetch("SELECT user_id, data FROM users")
        await conn.close()

        if not rows:
            await update.message.reply_text("Hozircha foydalanuvchilar mavjud emas.")
            return

        user_list = "\n".join([
            f"ID: {row['user_id']}, Username: {'@' + json.loads(row['data'])['username'] if json.loads(row['data'])['username'] else 'None'}, Name: {json.loads(row['data'])['first_name']}"
            for row in rows
        ])

        await update.message.reply_text(f"Barcha foydalanuvchilar:\n{user_list}")
    else:
        await update.message.reply_text("Sizda bu buyruqni ishlatish huquqi yo'q.")


async def channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id == ADMIN_ID:
        conn = await connect_db()
        rows = await conn.fetch("SELECT channel_username FROM channels")
        await conn.close()

        if rows:
            channel_list = "\n".join(f"@{row['channel_username']}" for row in rows)
            await update.message.reply_text(f"Mavjud kanallar:\n{channel_list}")
        else:
            await update.message.reply_text("Hozircha hech qanday kanal mavjud emas.")
    else:
        await update.message.reply_text("Sizda bu buyruqni ishlatish huquqi yo'q.")


async def admin_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id == ADMIN_ID:
        commands_list = (
            "/start - Botni boshlash\n"
            "/stat - Botning statistikasi\n"
            "/top - Eng ko'p tomosha qilingan filmlar\n"
            "/users - Barcha foydalanuvchilar ro'yxatini ko'rsatish\n"
            "/addchannel - Kanal qo'shish\n"
            "/removechannel - Kanal olib tashlash\n"
            "/channels - Mavjud kanallar ro'yxatini ko'rsatish\n"
            "/broadcast - Barcha foydalanuvchilarga xabar yuborish\n"
            "/admin - Barcha komandalarni ko'rsatish"
        )
        await update.message.reply_text(f"Barcha admin komandalar:\n{commands_list}")
    else:
        await update.message.reply_text("Admin bilan bog'lanish: @Bahromjon_Py.")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Faqat admin xabar jo‘nata oladi
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("Sizda bu buyruqni ishlatish huquqi yo'q.")
        return

    # Xabar javob sifatida yuborilganligini tekshirish
    if not update.message.reply_to_message:
        await update.message.reply_text("Iltimos, xabar yoki media orqali javob qiling.")
        return

    # Javob xabarini olish
    reply_message = update.message.reply_to_message
    failed_users = []

    # PostgreSQL bazasidan foydalanuvchilarni olish
    conn = await connect_db()
    rows = await conn.fetch("SELECT user_id FROM users")
    user_ids = [row["user_id"] for row in rows]
    await conn.close()

    # Barcha foydalanuvchilarga xabar yuborish
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
            logging.warning(f"Failed to send message to user {user_id}: {e}")
            failed_users.append(user_id)

    # Adminni natija haqida xabardor qilish
    if failed_users:
        await update.message.reply_text(
            f"Xabarni ushbu foydalanuvchilarga yuborib bo‘lmadi: {', '.join(map(str, failed_users))}")
    else:
        await update.message.reply_text("Xabar muvaffaqiyatli yuborildi.")


# Statistika komandasi - foydalanuvchilar va kinolar sonini chiqarish
async def stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # PostgreSQL bazasiga ulanib, ma'lumotlarni olish
    conn = await connect_db()

    # Foydalanuvchilar sonini olish
    users_count = await conn.fetchval("SELECT COUNT(*) FROM users")

    # Kinolar sonini olish
    movies_count = await conn.fetchval("SELECT COUNT(*) FROM message_store")

    await conn.close()

    # Natijani yuborish
    await update.message.reply_text(f"📊 Bot statistikasi:\n"
                                    f"👤 Foydalanuvchilar soni: {users_count}\n"
                                    f"🎬 Mavjud kinolar soni: {movies_count}\n"
                                    f"📌 Kinolar kanali: @movies_reel")


import asyncio
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters


async def main():
    await load_message_store()
    await load_users()
    await load_channels()

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stat", stat))
    application.add_handler(CommandHandler("top", top_movies))
    application.add_handler(CommandHandler("users", list_users))
    application.add_handler(CommandHandler("addchannel", add_channel))
    application.add_handler(CommandHandler("removechannel", remove_channel))
    application.add_handler(CommandHandler("channels", channels))
    application.add_handler(CommandHandler("admin", admin_commands))
    application.add_handler(CommandHandler("broadcast", broadcast))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, find_movie))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))
    application.add_handler(CallbackQueryHandler(button_callback))

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    for handler in application.handlers[0]:
        print(handler)

    try:
        await asyncio.Event().wait()
    finally:
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())  # Faqat bitta event loop ishga tushadi
