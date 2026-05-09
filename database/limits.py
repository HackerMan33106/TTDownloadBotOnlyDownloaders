"""
Работа с лимитами пользователей
"""
import aiosqlite
from datetime import datetime, timezone, timedelta
from config.settings import DB_PATH, PERMANENT_ADMIN
from database.admins import get_all_admins


async def set_user_limit(user_id: int, max_uses: int):
    """Устанавливает лимит использований для пользователя"""
    utc_plus_1 = timezone(timedelta(hours=1))
    current_time = datetime.now(utc_plus_1).isoformat()

    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO user_limits (user_id, max_uses, current_uses, last_reset) VALUES (?, ?, 0, ?)",
            (user_id, max_uses, current_time)
        )
        await conn.commit()


def get_time_until_reset() -> str:
    """Возвращает строку с временем до сброса"""
    utc_plus_1 = timezone(timedelta(hours=1))
    current_time = datetime.now(utc_plus_1)
    
    # Время до полуночи
    midnight = (current_time + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    time_diff = midnight - current_time
    
    hours = time_diff.seconds // 3600
    minutes = (time_diff.seconds % 3600) // 60
    
    # Склонение для часов
    if hours % 10 == 1 and hours % 100 != 11:
        hour_word = "час"
    elif hours % 10 in [2, 3, 4] and hours % 100 not in [12, 13, 14]:
        hour_word = "часа"
    else:
        hour_word = "часов"
    
    # Склонение для минут
    if minutes % 10 == 1 and minutes % 100 != 11:
        minute_word = "минуту"
    elif minutes % 10 in [2, 3, 4] and minutes % 100 not in [12, 13, 14]:
        minute_word = "минуты"
    else:
        minute_word = "минут"
    
    return f"Через {hours} {hour_word} и {minutes} {minute_word}"


async def get_user_limit(user_id: int):
    """Получает информацию о лимите пользователя"""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT max_uses, current_uses, last_reset FROM user_limits WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
    return row if row else None


async def check_and_increment_usage(user_id: int) -> bool:
    """Проверяет и увеличивает счётчик использований"""
    # Постоянный админ не имеют ограничений
    if user_id in PERMANENT_ADMIN:
        return True
    # Остальные админы из базы тоже не имеют ограничений
    if user_id in await get_all_admins():
        return True

    limit_data = await get_user_limit(user_id)
    if not limit_data:
        return True  # Нет ограничений

    max_uses, current_uses, last_reset = limit_data
    utc_plus_1 = timezone(timedelta(hours=1))
    current_time = datetime.now(utc_plus_1)
    last_reset_time = datetime.fromisoformat(last_reset)

    # Проверяем, прошли ли сутки (сброс в 00:00 UTC+1)
    if current_time.date() > last_reset_time.date():
        # Сбрасываем счётчик
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                "UPDATE user_limits SET current_uses = 1, last_reset = ? WHERE user_id = ?",
                (current_time.isoformat(), user_id)
            )
            await conn.commit()
        return True

    # Проверяем лимит
    if current_uses >= max_uses:
        return False

    # Увеличиваем счётчик
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE user_limits SET current_uses = current_uses + 1 WHERE user_id = ?",
            (user_id,)
        )
        await conn.commit()
    return True


async def remove_user_limit(user_id: int):
    """Удаляет ограничение для пользователя"""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM user_limits WHERE user_id = ?", (user_id,))
        await conn.commit()


async def decrement_usage(user_id: int):
    """Уменьшает счётчик использований"""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE user_limits SET current_uses = MAX(0, current_uses - 1) WHERE user_id = ?",
            (user_id,)
        )
        await conn.commit()


async def get_all_limited_users() -> list:
    """Получает список всех пользователей с ограничениями"""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT user_id FROM user_limits") as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def is_blacklisted(user_id: int) -> bool:
    """
    Проверяет, находится ли пользователь в blacklist
    Blacklist = max_uses = 0
    """
    limit_data = await get_user_limit(user_id)
    if not limit_data:
        return False

    max_uses = limit_data[0]
    return max_uses == 0

async def add_to_global_blacklist(user_id: int, reason: str = "") -> bool:
    """Добавляет пользователя в чёрный список"""
    async with aiosqlite.connect(DB_PATH) as conn:
        banned_at = datetime.now(timezone(timedelta(hours=1))).isoformat()
        await conn.execute(
            "INSERT OR REPLACE INTO global_blacklist (user_id, reason, banned_at) VALUES (?, ?, ?)",
            (user_id, reason, banned_at)
        )
        await conn.commit()
    return True


async def remove_from_global_blacklist(user_id: int) -> bool:
    """Удаляет пользователя из чёрного списка"""
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute("DELETE FROM global_blacklist WHERE user_id = ?", (user_id,))
        affected = cursor.rowcount
        await conn.commit()
    return affected > 0


async def is_globally_banned(user_id: int) -> bool:
    """Проверяет, находится ли пользователь в чёрном списке"""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT 1 FROM global_blacklist WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
    return row is not None


async def get_global_blacklist() -> list:
    """Получает список всех пользователей в чёрном списке"""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT user_id, reason, banned_at FROM global_blacklist") as cursor:
            rows = await cursor.fetchall()
    return rows

