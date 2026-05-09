"""
Работа с whitelist в базе данных
"""
import aiosqlite
from config.settings import DB_PATH, WHITELIST_USERS, WHITELIST_GROUPS


async def add_whitelist_user(user_id: int) -> bool:
    """Добавляет пользователя в whitelist"""
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (user_id,))
        await conn.commit()
        return cursor.rowcount > 0


async def remove_whitelist_user(user_id: int) -> bool:
    """Удаляет пользователя из whitelist"""
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute("DELETE FROM whitelist WHERE user_id = ?", (user_id,))
        await conn.commit()
        return cursor.rowcount > 0


async def get_all_whitelist_users() -> list:
    """Получает список всех пользователей в whitelist из БД"""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT user_id FROM whitelist") as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def is_user_whitelisted(user_id: int) -> bool:
    """Проверяет, есть ли пользователь в whitelist (БД + конфиг)"""
    # Сначала проверяем конфиг (статичный список)
    if WHITELIST_USERS and user_id in WHITELIST_USERS:
        return True

    # Затем проверяем БД
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT 1 FROM whitelist WHERE user_id = ?", (user_id,)) as cursor:
            result = await cursor.fetchone()
    return result is not None


async def get_combined_whitelist_users() -> tuple:
    """
    Возвращает кортеж из двух списков:
    1. Пользователи из конфига (статичные)
    2. Пользователи из БД (добавленные через команду)
    """
    db_users = await get_all_whitelist_users()
    config_users = list(WHITELIST_USERS) if WHITELIST_USERS else []
    return config_users, db_users


# Функции для групп
async def add_whitelist_group(chat_id: int) -> bool:
    """Добавляет группу в whitelist"""
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute("INSERT OR IGNORE INTO whitelist_groups (chat_id) VALUES (?)", (chat_id,))
        await conn.commit()
        return cursor.rowcount > 0


async def remove_whitelist_group(chat_id: int) -> bool:
    """Удаляет группу из whitelist"""
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute("DELETE FROM whitelist_groups WHERE chat_id = ?", (chat_id,))
        await conn.commit()
        return cursor.rowcount > 0


async def get_all_whitelist_groups() -> list:
    """Получает список всех групп в whitelist из БД"""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT chat_id FROM whitelist_groups") as cursor:
            rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def is_group_whitelisted(chat_id: int) -> bool:
    """Проверяет, есть ли группа в whitelist (БД + конфиг)"""
    # Сначала проверяем конфиг (статичный список)
    if WHITELIST_GROUPS and chat_id in WHITELIST_GROUPS:
        return True

    # Затем проверяем БД
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT 1 FROM whitelist_groups WHERE chat_id = ?", (chat_id,)) as cursor:
            result = await cursor.fetchone()
    return result is not None


async def get_user_id_by_username(username: str) -> int | None:
    """Получает user_id по username из справочника пользователей"""
    clean_username = username.lstrip('@').lower()

    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT user_id FROM users_directory WHERE username_lower = ?",
            (clean_username,)
        ) as cursor:
            result = await cursor.fetchone()

    return result[0] if result else None


async def search_user_by_name(name: str) -> int | None:
    """Ищет пользователя по имени (first_name или username) в справочнике"""
    name_lower = name.lower().strip()

    async with aiosqlite.connect(DB_PATH) as conn:
        # Ищем по имени или username
        async with conn.execute(
            "SELECT user_id FROM users_directory WHERE LOWER(first_name) = ? OR username_lower = ?",
            (name_lower, name_lower)
        ) as cursor:
            result = await cursor.fetchone()

    return result[0] if result else None


async def get_user_info_from_db(user_id: int) -> tuple | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT username, first_name FROM users_directory WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            result = await cursor.fetchone()
    return result


async def add_user_to_directory(user_id: int, username: str = None, first_name: str = None):
    async with aiosqlite.connect(DB_PATH) as conn:
        username_lower = username.lower() if username else None
        await conn.execute(
            "INSERT OR REPLACE INTO users_directory (user_id, username, username_lower, first_name) VALUES (?, ?, ?, ?)",
            (user_id, username, username_lower, first_name or username or str(user_id))
        )
        await conn.commit()


async def update_user_in_directory(user_id: int, username: str = None, first_name: str = None):
    async with aiosqlite.connect(DB_PATH) as conn:
        username_lower = username.lower() if username else None
        await conn.execute(
            "UPDATE users_directory SET username = ?, username_lower = ?, first_name = ? WHERE user_id = ?",
            (username, username_lower, first_name, user_id)
        )
        await conn.commit()


# Хранилище для ожидающих редактирования (in-memory)
_pending_edits: dict = {}


def set_pending_edit(admin_id: int, edit_type: str, target_id: int):
    """Устанавливает ожидание редактирования"""
    _pending_edits[admin_id] = {'type': edit_type, 'target_id': target_id}


def get_pending_edit(admin_id: int) -> dict | None:
    """Получает данные ожидающего редактирования"""
    return _pending_edits.get(admin_id)


def clear_pending_edit(admin_id: int):
    """Очищает ожидание редактирования"""
    if admin_id in _pending_edits:
        del _pending_edits[admin_id]


# Алиас для совместимости
is_in_whitelist = is_user_whitelisted
