import asyncio
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError

from config.settings import BOT_TOKEN, logger, DEBUG_MODE, USE_LOCAL_API, LOCAL_API_URL
from database.db import init_db
from database.audio import load_audio_storage
from handlers import (
    register_command_handlers,
    register_callback_handlers,
    register_message_handlers
)
from utils.crypto import init_callbacks_table, secure_callback
from aiogram.types import InlineKeyboardButton

_orig_ikb_init = InlineKeyboardButton.__init__

def _patched_ikb_init(self, **kwargs):
    if 'callback_data' in kwargs and isinstance(kwargs['callback_data'], str):
        kwargs['callback_data'] = secure_callback(kwargs['callback_data'])
    _orig_ikb_init(self, **kwargs)

InlineKeyboardButton.__init__ = _patched_ikb_init

async def main():
    """Главная функция запуска бота"""
    await init_db()
    init_callbacks_table()

    # Загрузка кэшей из базы данных
    await load_audio_storage()

    # Создаем оптимизированный connector с Keep-Alive
    import aiohttp
    connector = aiohttp.TCPConnector(
        limit=100,              # Максимум 100 одновременных соединений
        limit_per_host=30,      # До 30 соединений на один хост
        ttl_dns_cache=300,      # Кэш DNS на 5 минут
        force_close=False,      # Keep-Alive включен
        enable_cleanup_closed=True
    )

    # Создание бота с сессией и таймаутами
    if USE_LOCAL_API:
        from aiogram.client.telegram import TelegramAPIServer
        local_server = TelegramAPIServer.from_base(LOCAL_API_URL)
        # Большой таймаут для отправки тяжёлых файлов через локальный API
        timeout = aiohttp.ClientTimeout(total=600, connect=10, sock_read=60)
        session = AiohttpSession(api=local_server)
        session._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        logger.info(f"🚀 Используется локальный Bot API сервер: {LOCAL_API_URL}")
        logger.info("📁 Лимит файлов: 2000 MB")
    else:
        timeout = aiohttp.ClientTimeout(total=120, connect=10, sock_read=60)
        session = AiohttpSession()
        session._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        logger.info("☁️ Используется облачный Telegram API (лимит: 50 MB)")

    logger.info("⚡ Keep-Alive включен")

    bot = Bot(token=BOT_TOKEN, session=session)
    dp = Dispatcher()

    # Регистрация middleware
    from middleware.security import CallbackSecurityMiddleware
    dp.callback_query.outer_middleware(CallbackSecurityMiddleware())
    
    # Регистрация основных обработчиков
    register_message_handlers(dp)
    register_command_handlers(dp)
    register_callback_handlers(dp)
    
    # Запуск polling с обработкой ошибок
    while True:
        try:
            await dp.start_polling(
                bot, skip_updates=True,
                allowed_updates=["message", "callback_query", "edited_message", "channel_post", "inline_query"]
            )
        except TelegramNetworkError as e:
            logger.error(f"⚠️ Ошибка сети: {e}, перезапуск через 5 секунд...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"❌ Критическая ошибка: {e}")
            raise

if __name__ == "__main__":
    asyncio.run(main())