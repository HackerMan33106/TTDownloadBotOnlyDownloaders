import aiosqlite
from config.settings import DB_PATH, logger

async def init_db():
    """Инициализация таблиц базы данных (Облегченная версия)"""
    async with aiosqlite.connect(DB_PATH) as conn:
        # 1. Поддержание информации о пользователях
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            language_code TEXT,
            is_premium BOOLEAN,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 2. Белый список (/wl)
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS whitelist (
            id INTEGER PRIMARY KEY,
            user_id INTEGER UNIQUE,
            username TEXT,
            added_by INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 2.1. Белый список групп
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS whitelist_groups (
            id INTEGER PRIMARY KEY,
            chat_id INTEGER UNIQUE,
            chat_title TEXT,
            added_by INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 3. Лимиты (/bl)
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS user_limits (
            user_id INTEGER PRIMARY KEY,
            max_uses INTEGER,
            current_uses INTEGER DEFAULT 0,
            last_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS global_blacklist (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 4. Кеш аудио
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS audio_url_cache (
            file_unique_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 5. Админы
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            added_by INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 6. Кэш медиафайлов
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS media_cache (
            url TEXT PRIMARY KEY,
            video_file_id TEXT,
            audio_file_id TEXT,
            url_hash TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # Создаем индекс для url_hash для быстрого поиска
        await conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_media_cache_url_hash ON media_cache(url_hash)
        ''')

        # 7. Кэш слайдшоу
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS slideshow_cache (
            url TEXT PRIMARY KEY,
            photo_file_ids TEXT NOT NULL,
            audio_file_id TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 8. Хранилище аудио URL
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS audio_url_storage (
            audio_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 9. Хранилище скачанных аудио
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS audio_downloaded (
            audio_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 10. Справочник пользователей (для поиска по username/имени)
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS users_directory (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            username_lower TEXT,
            first_name TEXT,
            last_name TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # Создаем индекс для быстрого поиска по username_lower
        await conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_users_directory_username_lower
        ON users_directory(username_lower)
        ''')

        await conn.commit()
    logger.info("✅ База данных инициализирована")

async def get_media_cache(url_or_hash: str):
    """Получает запись из кэша по очищенному URL или хешу"""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT video_file_id, audio_file_id FROM media_cache WHERE url = ? OR url_hash = ?",
            (url_or_hash, url_or_hash)
        ) as cursor:
            result = await cursor.fetchone()
    return result

async def set_media_cache(url: str, video_id: str, audio_id: str):
    """Сохраняет/обновляет кэш медиафайлов"""
    import hashlib
    url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute('''
            INSERT INTO media_cache (url, video_file_id, audio_file_id, url_hash)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                video_file_id=excluded.video_file_id,
                audio_file_id=excluded.audio_file_id,
                url_hash=excluded.url_hash
        ''', (url, video_id, audio_id, url_hash))
        await conn.commit()

async def delete_media_cache(url_or_hash: str):
    """Удаляет запись из кэша по очищенному URL или хешу"""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "DELETE FROM media_cache WHERE url = ? OR url_hash = ?",
            (url_or_hash, url_or_hash)
        )
        await conn.commit()

async def get_slideshow_cache(url: str):
    """Получает кэш слайдшоу по URL"""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT photo_file_ids, audio_file_id FROM slideshow_cache WHERE url = ?",
            (url,)
        ) as cursor:
            result = await cursor.fetchone()
    if result:
        # Преобразуем строку file_ids обратно в список
        import json
        photo_ids = json.loads(result[0])
        return (photo_ids, result[1])
    return None

async def set_slideshow_cache(url: str, photo_file_ids: list, audio_file_id: str = None):
    """Сохраняет кэш слайдшоу"""
    import json
    photo_ids_json = json.dumps(photo_file_ids)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute('''
            INSERT INTO slideshow_cache (url, photo_file_ids, audio_file_id)
            VALUES (?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                photo_file_ids=excluded.photo_file_ids,
                audio_file_id=excluded.audio_file_id
        ''', (url, photo_ids_json, audio_file_id))
        await conn.commit()

async def delete_slideshow_cache(url: str):
    """Удаляет кэш слайдшоу по URL"""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM slideshow_cache WHERE url = ?", (url,))
        await conn.commit()