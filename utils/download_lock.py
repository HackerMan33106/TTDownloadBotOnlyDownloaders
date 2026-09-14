"""
Модуль синхронизации и дедупликации параллельных загрузок.
Предотвращает повторное одновременное скачивание одного и того же URL разными пользователями.
"""
import asyncio
from typing import Dict, Tuple
from config.settings import logger, is_debug_mode

# Активные события загрузки по нормализованному URL
_active_downloads: Dict[str, asyncio.Event] = {}
_lock = asyncio.Lock()


async def acquire_download_lock(url: str) -> Tuple[bool, asyncio.Event]:
    """
    Пытается захватить блокировку загрузки для указанного URL.
    
    Возвращает:
        (True, event)  - если данный запрос первый и должен выполнить загрузку.
        (False, event) - если загрузка уже выполняется другим запросом. В этом случае
                         вызывающий должен ожидать завершения: `await event.wait()`.
    """
    async with _lock:
        if url in _active_downloads:
            if is_debug_mode():
                logger.info(f"🔒 [Deduplication] URL уже загружается другим процессом: {url}")
            return False, _active_downloads[url]
        
        event = asyncio.Event()
        _active_downloads[url] = event
        if is_debug_mode():
            logger.info(f"🔑 [Deduplication] Захвачена блокировка загрузки для URL: {url}")
        return True, event


async def release_download_lock(url: str) -> None:
    """
    Освобождает блокировку загрузки для URL и оповещает всех ожидающих (event.set()).
    Безопасна при повторных вызовах или если URL не найден.
    """
    async with _lock:
        event = _active_downloads.pop(url, None)
        if event:
            event.set()
            if is_debug_mode():
                logger.info(f"🔓 [Deduplication] Освобождена блокировка и оповещены ожидающие для URL: {url}")


def is_url_downloading(url: str) -> bool:
    """Проверяет, скачивается ли URL прямо сейчас"""
    return url in _active_downloads
