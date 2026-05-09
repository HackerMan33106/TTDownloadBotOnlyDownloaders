"""
Модуль команд бота
"""
from aiogram import Router

from .diagnostics import router as diagnostics_router
from .info import router as info_router
from .admin_management import router as admin_router
from .whitelist_management import router as whitelist_router
from .download_video import router as download_video_router
from .storage import router as storage_router

def get_commands_router() -> Router:
    """Объединяет все роутеры команд"""
    main_router = Router()

    main_router.include_router(diagnostics_router)
    main_router.include_router(info_router)
    main_router.include_router(admin_router)
    main_router.include_router(whitelist_router)
    main_router.include_router(download_video_router)
    main_router.include_router(storage_router)

    return main_router

__all__ = [
    'get_commands_router'
]
