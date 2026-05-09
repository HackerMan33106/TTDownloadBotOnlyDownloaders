"""
Точка входа для команд (временная совместимость)
Использует модульную структуру из handlers/commands/
"""
from aiogram import Dispatcher
from .commands import get_commands_router


def register_command_handlers(dp: Dispatcher):
    """Регистрирует все обработчики команд"""
    commands_router = get_commands_router()
    dp.include_router(commands_router)
