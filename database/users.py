"""
Работа с пользователями в базе данных
"""
import aiosqlite
from aiogram import types
from config.settings import DB_PATH


async def ruser(user: types.User):
    if not user:
        return

    if user.is_bot:
        return

    username = user.username if user.username else None
    username_lower = user.username.lower() if user.username else None

    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO users_directory (user_id, username, username_lower, first_name) VALUES (?, ?, ?, ?)",
            (user.id, username, username_lower, user.first_name)
        )
        await conn.commit()


async def get_target_user(message: types.Message, command_args: str = None) -> tuple:
    target_id = None
    target_name = "Пользователь"

    # 1. Если это РЕПЛАЙ (ответ на сообщение)
    if message.reply_to_message:
        user = message.reply_to_message.from_user

        if user.is_bot:
            return None, None

        await ruser(user)
        return user.id, user.first_name

    # 2. Если есть аргументы (например /bl @username или /bl 12345)
    if command_args:
        args = command_args.strip()

        if args.isdigit():
            target_id = int(args)

            try:
                chat_info = await message.bot.get_chat(target_id)
                if chat_info.type == "private":
                    try:
                        member = await message.bot.get_chat_member(target_id, target_id)
                        if member.user.is_bot:
                            return None, None
                    except Exception:
                        pass
                target_name = chat_info.first_name or "Пользователь"
            except Exception:
                async with aiosqlite.connect(DB_PATH) as conn:
                    async with conn.execute(
                        "SELECT first_name FROM users_directory WHERE user_id = ?",
                        (target_id,)
                    ) as cursor:
                        res = await cursor.fetchone()
                if res:
                    target_name = res[0]

            return target_id, target_name

        # Вариант Б: Передан @username
        if args.startswith("@"):
            clean_username = args[1:].lower()
            async with aiosqlite.connect(DB_PATH) as conn:
                async with conn.execute(
                    "SELECT user_id, first_name FROM users_directory WHERE username_lower = ?",
                    (clean_username,)
                ) as cursor:
                    res = await cursor.fetchone()
            if res:
                try:
                    member = await message.bot.get_chat_member(res[0], res[0])
                    if member.user.is_bot:
                        return None, None
                except Exception:
                    pass
                return res[0], res[1]
            else:
                try:
                    chat_info = await message.bot.get_chat(args)
                    try:
                        member = await message.bot.get_chat_member(chat_info.id, chat_info.id)
                        if member.user.is_bot:
                            return None, None
                    except Exception:
                        pass
                    return chat_info.id, (chat_info.first_name or args)
                except Exception:
                    return None, None

        # Вариант В: Text Mention (ссылка на пользователя без юзернейма)
        if message.entities:
            for entity in message.entities:
                if entity.type == "text_mention" and entity.user:
                    if entity.user.is_bot:
                        return None, None
                    return entity.user.id, entity.user.first_name

    return None, None


async def get_all_users() -> list:
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT user_id FROM users_directory") as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def user_exists(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT 1 FROM users_directory WHERE user_id = ? LIMIT 1",
            (user_id,)
        ) as cursor:
            result = await cursor.fetchone()
    return result is not None
