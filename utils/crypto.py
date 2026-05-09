import sqlite3
import uuid
from typing import Optional

from config.settings import DB_PATH, logger

from collections import OrderedDict
MAX_CACHE_SIZE = 10000
_callback_cache = OrderedDict()


def init_callbacks_table():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = OFF")
            conn.execute('''
                CREATE TABLE IF NOT EXISTS secure_callbacks (
                    short_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute("DELETE FROM secure_callbacks WHERE datetime(created_at) < datetime('now', '-30 days')")
            conn.commit()
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации таблицы secure_callbacks: {e}")


def secure_callback(data: str) -> str:
    if not data or data.startswith("sec:"):
        return data
        
    short_id = uuid.uuid4().hex[:16]

    _callback_cache[short_id] = data
    if len(_callback_cache) > MAX_CACHE_SIZE:
        _callback_cache.popitem(last=False)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA synchronous = OFF")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                "INSERT INTO secure_callbacks (short_id, data) VALUES (?, ?)",
                (short_id, data)
            )
            conn.commit()
            logger.debug(f"✅ Сохранён callback: {short_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения secure_callback {short_id}: {e}")
    return f"sec:{short_id}"


def verify_callback(callback_data: str) -> Optional[str]:
    if not callback_data:
        return callback_data
        
    if callback_data.startswith("sec:"):
        short_id = callback_data[4:]
        
        if short_id in _callback_cache:
            _callback_cache.move_to_end(short_id)
            return _callback_cache[short_id]

        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("PRAGMA synchronous = OFF")
                conn.execute("PRAGMA journal_mode = WAL")
                cursor = conn.cursor()
                cursor.execute("SELECT data FROM secure_callbacks WHERE short_id = ?", (short_id,))
                row = cursor.fetchone()
                if row:
                    _callback_cache[short_id] = row[0]
                    if len(_callback_cache) > MAX_CACHE_SIZE:
                        _callback_cache.popitem(last=False)
                    logger.debug(f"✅ Найден callback в БД: {short_id}")
                    return row[0]
                else:
                    logger.warning(f"⚠️ Callback не найден в БД: {short_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка проверки secure_callback {short_id}: {e}")
            
        return None
        
    logger.warning(f"🚫 Заблокирована попытка использовать старую кнопку без подписи: {callback_data}")
    return None
