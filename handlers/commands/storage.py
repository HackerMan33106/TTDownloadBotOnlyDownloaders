"""
Команда /storage - информация о хранилище
"""
import os
import asyncio
from datetime import datetime
from aiogram import Router, types, Bot
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config.settings import logger, DB_PATH
from database.admins import is_admin
from database.whitelist import is_user_whitelisted
from database.audio import audio_url_storage, audio_downloaded
from utils.helpers import get_disk_usage, get_temp_dir_size, TEMP_DIR

router = Router()

# Словарь для хранения задач автообновления
_storage_autoupdate_tasks = {}


def _build_storage_text() -> str:
    """Собирает текст для /storage"""
    disk = get_disk_usage()
    temp_size, temp_files = get_temp_dir_size()
    temp_mb = temp_size / (1024 * 1024)

    text = "📊 Использование хранилища:\n\n"

    if disk:
        text += f"💾 Диск:\n"
        text += f"  Всего: {disk['total_gb']:.1f} GB\n"
        text += f"  Занято: {disk['used_gb']:.1f} GB ({disk['used_percent']:.1f}%)\n"
        text += f"  Свободно: {disk['free_gb']:.1f} GB\n\n"

    text += f"📁 Временные файлы ({TEMP_DIR}):\n"
    text += f"  Размер: {temp_mb:.1f} MB\n"
    text += f"  Файлов: {temp_files}\n\n"

    db_size = os.path.getsize(DB_PATH) / (1024 * 1024) if os.path.exists(DB_PATH) else 0
    text += f"🗄️ База данных:\n"
    text += f"  Размер: {db_size:.2f} MB\n"
    text += f"  URL в хранилище: {len(audio_url_storage)}\n"
    text += f"  Скачанных аудио: {len(audio_downloaded)}\n"

    now = datetime.now().strftime('%H:%M:%S')
    text += f"\n🕐 Обновлено: {now}"
    return text


def _build_storage_keyboard(user_id: int, msg_id: int, auto_update: bool = False) -> InlineKeyboardMarkup:
    """Собирает клавиатуру для /storage"""
    from utils.crypto import secure_callback

    buttons = []
    if auto_update:
        buttons.append([InlineKeyboardButton(text="⏹ Остановить автообновление", callback_data=secure_callback("storage_auto:stop"))])
    else:
        buttons.append([InlineKeyboardButton(text="🔄 Автообновление (10 мин)", callback_data=secure_callback("storage_auto:start"))])
    buttons.append([InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_universal:{user_id}:{msg_id}"))])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _auto_update_storage(bot: Bot, chat_id: int, message_id: int, user_id: int):
    """Фоновая задача автообновления /storage каждые 10 минут"""
    try:
        while True:
            await asyncio.sleep(600)  # 10 минут
            try:
                text = _build_storage_text()
                keyboard = _build_storage_keyboard(user_id, 0, auto_update=True)
                await bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=keyboard
                )
            except Exception as e:
                # Сообщение удалено или другая ошибка — останавливаем
                logger.info(f"⏹ Автообновление /storage остановлено: {e}")
                break
    except asyncio.CancelledError:
        pass
    finally:
        key = (chat_id, message_id)
        _storage_autoupdate_tasks.pop(key, None)


@router.message(Command('storage'))
async def storage_command(message: types.Message):
    """Показывает информацию о хранилище"""
    user_id = message.from_user.id

    # Доступ для админов и whitelist
    if not (await is_admin(user_id) or await is_user_whitelisted(user_id)):
        return

    try:
        text = _build_storage_text()
        keyboard = _build_storage_keyboard(message.from_user.id, message.message_id)
        await message.reply(text, reply_markup=keyboard)
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


@router.callback_query(lambda c: c.data and c.data.startswith("storage_auto:"))
async def storage_auto_callback(callback: types.CallbackQuery, bot: Bot):
    """Управление автообновлением /storage"""
    # Доступ для админов и whitelist
    if not (await is_admin(callback.from_user.id) or await is_user_whitelisted(callback.from_user.id)):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    action = callback.data.split(":")[1]
    key = (callback.message.chat.id, callback.message.message_id)

    if action == "start":
        # Останавливаем старую задачу если есть
        if key in _storage_autoupdate_tasks:
            _storage_autoupdate_tasks[key].cancel()

        # Запускаем автообновление
        task = asyncio.create_task(
            _auto_update_storage(bot, callback.message.chat.id, callback.message.message_id, callback.from_user.id)
        )
        _storage_autoupdate_tasks[key] = task

        # Обновляем кнопку
        text = _build_storage_text()
        keyboard = _build_storage_keyboard(callback.from_user.id, 0, auto_update=True)
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except:
            pass
        await callback.answer("🔄 Автообновление включено (каждые 10 мин)")

    elif action == "stop":
        if key in _storage_autoupdate_tasks:
            _storage_autoupdate_tasks[key].cancel()
            del _storage_autoupdate_tasks[key]

        text = _build_storage_text()
        keyboard = _build_storage_keyboard(callback.from_user.id, 0, auto_update=False)
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except:
            pass
        await callback.answer("⏹ Автообновление остановлено")
