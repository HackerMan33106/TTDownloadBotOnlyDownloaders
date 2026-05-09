"""
Работа с аудио в базе данных
"""
import json
import aiosqlite
from config.settings import DB_PATH


# Глобальные хранилища (кэш)
audio_url_storage: dict = {}
audio_downloaded: dict = {}


async def save_audio_url_storage(audio_id: str, data: dict):
    """Сохраняет данные в audio_url_storage"""
    global audio_url_storage
    audio_url_storage[audio_id] = data

    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO audio_url_storage (audio_id, data) VALUES (?, ?)",
            (audio_id, json.dumps(data))
        )
        await conn.commit()


async def load_audio_url_storage() -> dict:
    """Загружает все данные из audio_url_storage"""
    global audio_url_storage

    async with aiosqlite.connect(DB_PATH) as conn:
        # Проверяем существует ли таблица
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audio_url_storage'"
        ) as cursor:
            if not await cursor.fetchone():
                return {}

        async with conn.execute("SELECT audio_id, data FROM audio_url_storage") as cursor:
            rows = await cursor.fetchall()

    storage = {}
    for audio_id, data in rows:
        storage[audio_id] = json.loads(data)

    audio_url_storage.clear()
    audio_url_storage.update(storage)
    return storage


async def delete_audio_url_storage(audio_id: str):
    """Удаляет запись из audio_url_storage"""
    global audio_url_storage
    if audio_id in audio_url_storage:
        del audio_url_storage[audio_id]

    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM audio_url_storage WHERE audio_id = ?", (audio_id,))
        await conn.commit()


async def save_audio_downloaded(audio_id: str, data: dict):
    """Сохраняет данные в audio_downloaded"""
    global audio_downloaded
    audio_downloaded[audio_id] = data

    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO audio_downloaded (audio_id, data) VALUES (?, ?)",
            (audio_id, json.dumps(data))
        )
        await conn.commit()


async def load_audio_downloaded() -> dict:
    """Загружает все данные из audio_downloaded"""
    global audio_downloaded

    async with aiosqlite.connect(DB_PATH) as conn:
        # Проверяем существует ли таблица
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audio_downloaded'"
        ) as cursor:
            if not await cursor.fetchone():
                return {}

        async with conn.execute("SELECT audio_id, data FROM audio_downloaded") as cursor:
            rows = await cursor.fetchall()

    downloaded = {}
    for audio_id, data in rows:
        downloaded[audio_id] = json.loads(data)

    audio_downloaded.clear()
    audio_downloaded.update(downloaded)
    return downloaded


async def delete_audio_downloaded(audio_id: str):
    """Удаляет запись из audio_downloaded"""
    global audio_downloaded
    if audio_id in audio_downloaded:
        del audio_downloaded[audio_id]

    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM audio_downloaded WHERE audio_id = ?", (audio_id,))
        await conn.commit()


def init_audio_storage():
    """Инициализация хранилищ аудио при запуске"""
    load_audio_url_storage()
    load_audio_downloaded()


async def load_audio_storage():
    """Алиас для init_audio_storage для совместимости"""
    from config.settings import logger

    url_storage = await load_audio_url_storage()
    downloaded = await load_audio_downloaded()

    logger.info(f"📦 Загружено из БД: {len(url_storage)} URL, {len(downloaded)} скачанных аудио")


async def clear_all_audio_data() -> int:
    """Очищает все данные аудио из БД и кэша"""
    global audio_url_storage, audio_downloaded

    async with aiosqlite.connect(DB_PATH) as conn:
        # Подсчитываем количество записей
        async with conn.execute("SELECT COUNT(*) FROM audio_url_storage") as cursor:
            count1 = (await cursor.fetchone())[0]
        async with conn.execute("SELECT COUNT(*) FROM audio_downloaded") as cursor:
            count2 = (await cursor.fetchone())[0]
        total = count1 + count2

        # Удаляем все записи
        await conn.execute("DELETE FROM audio_url_storage")
        await conn.execute("DELETE FROM audio_downloaded")
        await conn.commit()

    # Очищаем кэш
    audio_url_storage.clear()
    audio_downloaded.clear()

    return total
