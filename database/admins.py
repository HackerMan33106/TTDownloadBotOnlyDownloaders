"""
Работа с администраторами
"""
import aiosqlite
from config.settings import DB_PATH, PERMANENT_ADMIN


async def add_admin(user_id: int):
    """Добавляет админа"""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
        await conn.commit()


async def remove_admin(user_id: int):
    """Удаляет админа"""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await conn.commit()


async def get_all_admins() -> list:
    """Получает список всех админов"""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT user_id FROM admins") as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def is_admin(user_id: int) -> bool:
    """Проверяет является ли пользователь админом"""
    # Постоянный админ всегда админ
    if user_id in PERMANENT_ADMIN:
        return True
    # Остальные - только если в базе
    return user_id in await get_all_admins()
