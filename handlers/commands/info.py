"""
Информационные команды (/help, /ping)
"""
import time
from aiogram import Router, types
from aiogram.filters import Command

from utils.helpers import create_delete_button
from config.settings import PERMANENT_ADMIN

router = Router()

@router.message(Command('help'))
async def help_command(message: types.Message):
    """Команда /help - справка по всем командам бота"""
    
    help_text = (
        "📚 Справка по командам бота\n\n"
        
        "🔧 Основные команды:\n"
        "• /ping - проверка задержки бота\n"
        "• /help - эта справка\n\n"
        
        "📥 Загрузка контента:\n"
        "• /dw url - загрузка видео/аудио/фото (доступно всем)\n"
        "• /dw help - подробная справка по /dw\n\n"
    )
    
    if message.from_user.id in PERMANENT_ADMIN:
        help_text += (
            "Следующие команды доступны только администраторам:\n\n"

            "👑 Управление админами:\n"
            "• /admin - показать список админов\n"
            "• /admin -a ID|@username - добавить админа\n"
            "• /admin -r ID|@username - удалить админа\n"
            "• /admin help - подробная справка\n\n"
            
            "✅ Белый список:\n"
            "• /wl - показать белый список\n"
            "• /wl -a ID|@username - добавить в whitelist\n"
            "• /wl -r ID|@username - удалить из whitelist\n"
            "• /wl help - подробная справка\n\n"
            
            "🚫 Черный список:\n"
            "• /bl - показать черный список\n"
            "• /bl -a ID|@username количество - установить лимиты\n"
            "• /bl -r ID|@username - удалить из blacklist\n"
            "• /bl help - подробная справка\n\n"
        )
        
    await message.reply(help_text, parse_mode="HTML", reply_markup=create_delete_button(message))

@router.message(Command('ping'))
async def ping_command(message: types.Message):
    """Команда /ping - проверка задержки"""
    start = time.time()
    msg = await message.reply("Понг!")
    delay = round((time.time() - start) * 1000)
    
    emoji = "🟢" if delay <= 200 else "🟡" if delay <= 400 else "🟠" if delay <= 600 else "🔴"
    
    await msg.edit_text(
        f"Понг! {delay}мс {emoji}\nБот работает {'почти ' if emoji=='🔴' else ''}нормально.",
        reply_markup=create_delete_button(message)
    )

@router.message(Command('start'))
async def start_command(message: types.Message):
    """Команда /start"""
    await help_command(message)