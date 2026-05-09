from aiogram import Router, types, Bot, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command

from config.settings import DB_PATH, logger, PERMANENT_ADMIN
from database.whitelist import get_user_id_by_username, get_user_info_from_db
from utils.helpers import create_delete_button
from database.limits import (
    get_user_limit,
    set_user_limit,
    remove_user_limit,
    get_time_until_reset,
    is_blacklisted
)
import sqlite3

router = Router()

# Состояния для установки лимита
class BLStates(StatesGroup):
    waiting_limit_number = State()

_bl_pending_limit = {}

@router.message(Command('bl'))
async def blacklist_command(message: types.Message, bot: Bot):
    """Управление лимитами пользователей"""
    user_id = message.from_user.id
    is_user_admin = user_id in PERMANENT_ADMIN
    
    try:
        args = message.text.split()
        
        # Показываем инструкцию если написано /bl help
        if len(args) == 2 and args[1].lower() == 'help':
            if not is_user_admin:
                await message.reply(
                    "📖 Управление лимитами:\n\n"
                    "• /bl - проверить свой лимит использований",
                    reply_markup=create_delete_button(message)
                )
            else:
                await message.reply(
                    "📖 Управление лимитами:\n\n"
                    "Просмотр:\n"
                    "• /bl - показать список ограниченных пользователей\n"
                    "• /bl ID|@username - проверить лимит пользователя\n\n"
                    "Добавление лимита:\n"
                    "• /bl -a ID количество - установить лимит по ID\n"
                    "• /bl -a @username количество - установить лимит по username\n"
                    "• /bl -a ID 0 - заблокировать пользователя\n\n"
                    "Удаление лимита:\n"
                    "• /bl -r ID - удалить по ID\n"
                    "• /bl -r @username - удалить по username\n\n"
                    "Примеры:\n"
                    "• /bl -a 123456789 5 - лимит 5 использований в день\n"
                    "• /bl -a @user 0 - заблокировать пользователя",
                    parse_mode="HTML",
                    reply_markup=create_delete_button(message)
                )
            return
        
        # Если нет аргументов - показываем информацию
        if len(args) == 1:
            # Для обычного пользователя - показываем его лимит
            if not is_user_admin:
                limit_data = await get_user_limit(user_id)
                if limit_data:
                    max_uses, current_uses, _ = limit_data
                    if max_uses == 0:
                        await message.reply(
                            "🚫 Использование бота для вас заблокировано.",
                            reply_markup=create_delete_button(message)
                        )
                    else:
                        time_until_reset = get_time_until_reset()
                        await message.reply(
                            f"📊 Ваш лимит использований:\n"
                            f"✅ Использовано: {current_uses}/{max_uses}\n"
                            f"⏰ Сброс в 00:00 UTC+1. {time_until_reset}",
                            reply_markup=create_delete_button(message)
                        )
                else:
                    await message.reply(
                        "✨ У вас нет ограничений на использование бота",
                        reply_markup=create_delete_button(message)
                    )
                return
            
            # Для админа - показываем список ограниченных пользователей
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, max_uses, current_uses FROM user_limits")
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                await message.reply(
                    "📋 Нет ограниченных пользователей\n\n"
                    "💡 /bl help - справка",
                    reply_markup=create_delete_button(message)
                )
                return
            
            text = "📋 Ограниченные пользователи:\n\n"
            
            async def format_limited_user(uid: int, max_uses: int, current_uses: int) -> str:
                try:
                    from utils.helpers import transliterate_name
                    user_info = await bot.get_chat(uid)
                    user_first_name = user_info.first_name or "Unknown"
                    name = transliterate_name(user_first_name) if user_first_name != "Unknown" and not user_first_name.isascii() else user_first_name
                    uname = f"@{user_info.username}" if user_info.username else None

                    if max_uses == 0:
                        limit_text = "Заблокирован"
                    else:
                        limit_text = f"Лимит: {current_uses}/{max_uses} в день"

                    if uname:
                        return f"{uname} - {uid} ({name})\n   {limit_text}"
                    else:
                        return f"{name} - {uid}\n   {limit_text}"
                except:
                    from database.whitelist import get_user_info_from_db
                    from utils.helpers import transliterate_name
                    db_info = await get_user_info_from_db(uid)

                    if max_uses == 0:
                        limit_text = "Заблокирован"
                    else:
                        limit_text = f"Лимит: {current_uses}/{max_uses} в день"

                    if db_info:
                        username, first_name = db_info
                        display_name = transliterate_name(first_name) if first_name and not first_name.isascii() else first_name
                        if username:
                            return f"@{username} - {uid} ({display_name})\n   {limit_text}"
                        else:
                            return f"{display_name} - {uid}\n   {limit_text}"
                    else:
                        return f"{uid}\n   {limit_text}"
            
            for uid, max_uses, current_uses in rows:
                user_line = await format_limited_user(uid, max_uses, current_uses)
                text += f"• {user_line}\n\n"
            
            text += "💡 /bl help - справка по управлению лимитами"
            
            await message.reply(text, parse_mode="HTML", reply_markup=create_delete_button(message))
            return

        # /bl -a <ID|@username> <количество> — установить лимит
        if args[1].lower() == '-a':
            if not is_user_admin:
                return
            if len(args) < 4:
                await message.reply(
                    "❌ Неверный формат\n\n"
                    "Использование: /bl -a ID|@username количество\n"
                    "Пример: /bl -a 123456789 5",
                    reply_markup=create_delete_button(message)
                )
                return
            
            identifier = args[2].strip()
            try:
                max_uses = int(args[3])
            except ValueError:
                await message.reply("❌ Количество должно быть числом", reply_markup=create_delete_button(message))
                return
            
            target_id = None
            if identifier.startswith('@'):
                target_id = await get_user_id_by_username(identifier)
                if not target_id:
                    await message.reply(f"❌ Пользователь {identifier} не найден в базе", reply_markup=create_delete_button(message))
                    return
            elif identifier.isdigit():
                target_id = int(identifier)
            else:
                await message.reply("❌ Укажите ID или @username", reply_markup=create_delete_button(message))
                return
            
            # Проверка на бота
            try:
                t_chat = await bot.get_chat(target_id)
                if t_chat.is_bot:
                    await message.reply("❌ Ботов нельзя ограничивать.", reply_markup=create_delete_button(message))
                    return
            except:
                pass

            await set_user_limit(target_id, max_uses)
            
            # Получаем имя
            try:
                user_info = await bot.get_chat(target_id)
                name = user_info.first_name or str(target_id)
                uname = f" (@{user_info.username})" if user_info.username else ""
            except:
                db_info = await get_user_info_from_db(target_id)
                if db_info:
                    username, first_name = db_info
                    name = first_name or str(target_id)
                    uname = f" (@{username})" if username else ""
                else:
                    name = str(target_id)
                    uname = ""
            
            await message.reply(
                f"✅ Лимит установлен:\n"
                f"👤 {name}{uname}\n"
                f"🆔 ID: {target_id}\n"
                f"📊 Лимит: {max_uses} использований в день",
                parse_mode="HTML",
                reply_markup=create_delete_button(message)
            )
            return
        
        # /bl -r <ID|@username> — удалить лимит
        if args[1].lower() == '-r':
            if not is_user_admin:
                return
            if len(args) < 3:
                await message.reply(
                    "❌ Укажите кого разблокировать\n\n"
                    "Использование: /bl -r ID|@username",
                    reply_markup=create_delete_button(message)
                )
                return
            
            identifier = args[2].strip()
            target_id = None
            if identifier.startswith('@'):
                target_id = await get_user_id_by_username(identifier)
                if not target_id:
                    await message.reply(f"❌ Пользователь {identifier} не найден в базе", reply_markup=create_delete_button(message))
                    return
            elif identifier.isdigit():
                target_id = int(identifier)
            else:
                await message.reply("❌ Укажите ID или @username", reply_markup=create_delete_button(message))
                return
            
            limit_data = await get_user_limit(target_id)
            if not limit_data:
                await message.reply(f"ℹ️ У пользователя {target_id} нет лимита", reply_markup=create_delete_button(message))
                return
            
            await remove_user_limit(target_id)
            
            try:
                user_info = await bot.get_chat(target_id)
                name = user_info.first_name or str(target_id)
                uname = f" (@{user_info.username})" if user_info.username else ""
            except:
                db_info = await get_user_info_from_db(target_id)
                if db_info:
                    username, first_name = db_info
                    name = first_name or str(target_id)
                    uname = f" (@{username})" if username else ""
                else:
                    name = str(target_id)
                    uname = ""
            
            await message.reply(
                f"✅ Лимит удалён:\n"
                f"👤 {name}{uname}\n"
                f"🆔 ID: {target_id}",
                parse_mode="HTML",
                reply_markup=create_delete_button(message)
            )
            return
        
        # /bl <id> или /bl @username — показать информацию с кнопками
        if is_user_admin:
            identifier = args[1].strip()
            target_id = None
            display_name = identifier
            
            if identifier.startswith('@'):
                target_id = await get_user_id_by_username(identifier)
                if target_id:
                    display_name = identifier
            elif identifier.isdigit():
                target_id = int(identifier)
                try:
                    user_info = await bot.get_chat(target_id)
                    uname = f"@{user_info.username}" if user_info.username else ""
                    display_name = f"{user_info.first_name or target_id} {uname}".strip()
                except:
                    db_info = await get_user_info_from_db(target_id)
                    if db_info:
                        username, first_name = db_info
                        display_name = f"{first_name} (@{username})" if username else str(first_name or target_id)
                    else:
                        display_name = str(target_id)
            
            if target_id:
                limit_data = await get_user_limit(target_id)

                buttons = []
                if limit_data:
                    max_uses, current_uses, _ = limit_data
                    if max_uses == 0:
                        status = "🚫 Заблокирован"
                    else:
                        time_until_reset = get_time_until_reset()
                        status = f"🔒 Лимит: {current_uses}/{max_uses} в день\n⏰ Сброс: {time_until_reset}"
                    buttons.append([InlineKeyboardButton(text="🔓 Удалить лимит", callback_data=f"bl_action:remove:{target_id}:{message.message_id}")])
                else:
                    status = "✅ Без ограничений"
                    buttons.append([InlineKeyboardButton(text="🔒 Ограничить", callback_data=f"bl_action:ask_limit:{target_id}:{message.message_id}")])

                buttons.append([InlineKeyboardButton(text="🗑 Закрыть", callback_data=f"delete_universal:{user_id}:{message.message_id}")])

                keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
                await message.reply(
                    f"👤 {display_name}\n"
                    f"🆔 ID: {target_id}\n"
                    f"📊 {status}\n\n"
                    f"Выберите действие:",
                    reply_markup=keyboard
                )
                return
        
        await message.reply(
            "❌ Неизвестная команда\n\n"
            "Используйте /bl help для справки",
            reply_markup=create_delete_button(message)
        )
        
    except Exception as e:
        await message.reply(
            f"❌ Ошибка: {e}",
            reply_markup=create_delete_button(message)
        )
        logger.error(f"Ошибка в /bl: {e}")


@router.callback_query(F.data.startswith("bl_action:"))
async def bl_action_callback(callback: types.CallbackQuery, bot: Bot, state: FSMContext):
    """Обработчик inline-кнопок для управления лимитами"""
    if callback.from_user.id not in PERMANENT_ADMIN:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    parts = callback.data.split(":")
    action = parts[1]
    target_id = int(parts[2])
    cmd_msg_id = int(parts[3]) if len(parts) > 3 else 0
    
    # Получаем имя для сообщения
    try:
        user_info = await bot.get_chat(target_id)
        name = user_info.first_name or str(target_id)
    except:
        name = str(target_id)
    
    if action == "remove":
        limit_data = await get_user_limit(target_id)
        if not limit_data:
            await callback.answer("Лимит уже удалён", show_alert=True)
            return
        
        await remove_user_limit(target_id)
        await callback.answer("✅ Лимит удалён")
        
        try:
            await callback.message.edit_text(
                f"✅ Лимит удалён для {name} ({target_id})",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑 Закрыть", callback_data=f"delete_universal:{callback.from_user.id}:{cmd_msg_id}")]
                ])
            )
        except:
            pass
    
    elif action == "ask_limit":
        # Спрашиваем число — переходим в FSM
        _bl_pending_limit[callback.from_user.id] = {
            'target_id': target_id,
            'target_name': name,
            'cmd_msg_id': cmd_msg_id,
            'bot_msg_id': callback.message.message_id,
            'chat_id': callback.message.chat.id,
        }
        
        await callback.message.edit_text(
            f"🔒 Установка лимита для {name} ({target_id})\n\n"
            "Введите максимальное количество использований в день (число):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"delete_universal:{callback.from_user.id}:{cmd_msg_id}")]
            ])
        )
        await state.set_state(BLStates.waiting_limit_number)
        await callback.answer()
    
    elif action.startswith("set"):
        max_uses = int(action.replace("set", ""))
        await set_user_limit(target_id, max_uses)
        await callback.answer(f"✅ Лимит {max_uses} установлен")
        
        try:
            await callback.message.edit_text(
                f"🔒 Лимит {max_uses}/день установлен для {name} ({target_id})",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑 Закрыть", callback_data=f"delete_universal:{callback.from_user.id}:{cmd_msg_id}")]
                ])
            )
        except:
            pass


@router.message(BLStates.waiting_limit_number)
async def process_limit_number(message: types.Message, bot: Bot, state: FSMContext):
    """Обработчик ввода числа для установки лимита"""
    user_id = message.from_user.id

    if user_id not in PERMANENT_ADMIN:
        await state.clear()
        return

    if user_id not in _bl_pending_limit:
        await message.reply("❌ Сессия истекла. Используйте /bl заново.", reply_markup=create_delete_button(message))
        await state.clear()
        return

    pending = _bl_pending_limit[user_id]
    target_id = pending['target_id']
    target_name = pending['target_name']
    cmd_msg_id = pending['cmd_msg_id']
    bot_msg_id = pending['bot_msg_id']
    chat_id = pending['chat_id']

    try:
        max_uses = int(message.text.strip())
        if max_uses < 0:
            await message.reply("❌ Число должно быть неотрицательным", reply_markup=create_delete_button(message))
            return
    except ValueError:
        await message.reply("❌ Введите корректное число", reply_markup=create_delete_button(message))
        return

    await set_user_limit(target_id, max_uses)

    # Удаляем сообщение пользователя
    try:
        await message.delete()
    except:
        pass

    # Обновляем сообщение бота
    try:
        if max_uses == 0:
            status_text = f"🚫 Пользователь {target_name} ({target_id}) заблокирован"
        else:
            status_text = f"🔒 Лимит {max_uses}/день установлен для {target_name} ({target_id})"

        await bot.edit_message_text(
            text=status_text,
            chat_id=chat_id,
            message_id=bot_msg_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Закрыть", callback_data=f"delete_universal:{user_id}:{cmd_msg_id}")]
            ])
        )
    except Exception as e:
        logger.error(f"Ошибка обновления сообщения: {e}")

    # Очищаем состояние
    del _bl_pending_limit[user_id]
    await state.clear()