"""
Управление белым списком (/wl)
"""
from aiogram import Router, types, Bot, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command

from config.settings import WHITELIST_USERS, logger, PERMANENT_ADMIN
from database.admins import is_admin
from database.whitelist import (
    add_whitelist_user, remove_whitelist_user, get_all_whitelist_users,
    get_combined_whitelist_users, get_user_id_by_username, get_user_info_from_db,
    add_user_to_directory, set_pending_edit
)
from utils.helpers import create_delete_button


router = Router()


@router.message(Command('wl'))
async def whitelist_command(message: types.Message, bot: Bot):
    """Управление белым списком пользователей
    
    Команды:
    /wl - показать whitelist
    /wl -a <ID|@username> - добавить в whitelist
    /wl -r <ID|@username> - удалить из whitelist
    """
    if message.from_user.id not in PERMANENT_ADMIN:
        return
    
    user_id = message.from_user.id
    
    try:
        args = message.text.split()
        
        # /wl без аргументов - показать список
        if len(args) == 1:
            config_users, db_users = await get_combined_whitelist_users()
            
            text = "📋 Белый список пользователей:\n\n"
            
            # Вспомогательная функция для форматирования пользователя
            async def format_user(uid: int) -> str:
                try:
                    user_info = await bot.get_chat(uid)
                    name = user_info.first_name or "Unknown"
                    uname = f"@{user_info.username}" if user_info.username else None
                    if uname:
                        return f"{uname} - {uid} ({name})"
                    else:
                        return f"{name} - {uid}"
                except:
                    db_info = await get_user_info_from_db(uid)
                    if db_info:
                        username, first_name = db_info
                        if username:
                            return f"@{username} - {uid} ({first_name})"
                        else:
                            return f"{first_name} - {uid}"
                    else:
                        return f"{uid}"
            
            # Статичные из конфига
            if config_users:
                text += "🔒 Из конфига (статичные):\n"
                for wl_user_id in config_users:
                    text += f"  • {await format_user(wl_user_id)}\n"
            
            # Добавленные через команду
            if db_users:
                text += f"\n🟢 Добавленные через команду:\n"
                for wl_user_id in db_users:
                    text += f"  • {await format_user(wl_user_id)}\n"
            
            if not config_users and not db_users:
                text += "Список пуст\n"
            
            text += "\n💡 /wl help - справка"
            
            await message.reply(text, parse_mode="HTML", reply_markup=create_delete_button(message))
            return
        
        # /wl help - справка
        if args[1].lower() == 'help':
            await message.reply(
                "📖 Управление белым списком:\n\n"
                "Просмотр:\n"
                "• /wl - показать белый список\n\n"
                "Добавление:\n"
                "• /wl -a 123456789 - добавить по ID\n"
                "• /wl -a @username - добавить по username\n"
                "• /wl -a @username 123456789 - добавить с автодобавлением в БД\n"
                "• /wl -a @username 123456789 Name - с указанием имени\n\n"
                "Редактирование:\n"
                "• /wl -e 123456789 - редактировать по ID\n"
                "• /wl -e @username - редактировать по username\n\n"
                "Удаление:\n"
                "• /wl -r 123456789 - удалить по ID\n"
                "• /wl -r @username - удалить по username\n\n",
                parse_mode="HTML",
                reply_markup=create_delete_button(message)
            )
            return
        
        # /wl -e - редактирование
        if args[1].lower() == '-e':
            if len(args) < 3:
                await message.reply(
                    "❌ Укажите ID или @username\n\n"
                    "Примеры:\n"
                    "• /wl -e 123456789\n"
                    "• /wl -e @username",
                    reply_markup=create_delete_button(message)
                )
                return
            
            identifier = args[2].strip()
            
            # Определяем user_id
            if identifier.startswith('@'):
                target_id = await get_user_id_by_username(identifier)
                if not target_id:
                    await message.reply(
                        f"❌ Пользователь {identifier} не найден в базе данных",
                        reply_markup=create_delete_button(message)
                    )
                    return
            elif identifier.isdigit():
                target_id = int(identifier)
            else:
                await message.reply(
                    "❌ Неверный формат. Используйте ID или @username",
                    reply_markup=create_delete_button(message)
                )
                return
            
            # Проверяем что пользователь в whitelist
            all_wl = list(await get_all_whitelist_users())
            if WHITELIST_USERS:
                all_wl.extend(WHITELIST_USERS)
            
            if target_id not in all_wl:
                await message.reply(
                    f"❌ Пользователь с ID {target_id} не в whitelist",
                    reply_markup=create_delete_button(message)
                )
                return
            
            # Получаем текущую информацию
            db_info = await get_user_info_from_db(target_id)
            if db_info:
                username, first_name = db_info
                current_info = f"@{username} {target_id} {first_name}" if username else f"- {target_id} {first_name}"
            else:
                current_info = f"- {target_id} Unknown"
            
            # Устанавливаем ожидание редактирования
            set_pending_edit(user_id, 'wl', target_id)
            
            await message.reply(
                f"📝 Редактирование пользователя из whitelist:\n\n"
                f"Текущие данные: {current_info}\n\n"
                f"Отправьте новые данные в формате:\n"
                f"@username {target_id} Имя\n\n"
                f"⚠️ ID изменить нельзя",
                parse_mode="HTML",
                reply_markup=create_delete_button(message)
            )
            return
        
        # /wl -a - добавление
        if args[1].lower() == '-a':
            if len(args) < 3:
                await message.reply(
                    "❌ Укажите ID или @username\n\n"
                    "Примеры:\n"
                    "• /wl -a 123456789\n"
                    "• /wl -a @username\n"
                    "• /wl -a @username 123456789 - автодобавление в БД",
                    reply_markup=create_delete_button(message)
                )
                return
            
            identifier = args[2].strip()
            username_for_db = None
            first_name_for_db = None
            
            # Если указано 3+ аргумента - это добавление с автодобавлением в БД
            # Формат: /wl -a @username ID [Name]
            if len(args) >= 4 and args[3].isdigit():
                username_for_db = identifier.lstrip('@')
                target_id = int(args[3])
                first_name_for_db = ' '.join(args[4:]) if len(args) > 4 else username_for_db
                
                # Добавляем в БД пользователей
                await add_user_to_directory(target_id, username_for_db, first_name_for_db)
            else:
                # Определяем user_id
                if identifier.startswith('@'):
                    target_id = await get_user_id_by_username(identifier)
                    if not target_id:
                        await message.reply(
                            f"❌ Пользователь {identifier} не найден в базе данных\n\n"
                            "Используйте расширенный формат для автодобавления:\n"
                            f"/wl -a {identifier} ID\n"
                            f"/wl -a {identifier} ID Name",
                            parse_mode="HTML",
                            reply_markup=create_delete_button(message)
                        )
                        return
                elif identifier.isdigit():
                    target_id = int(identifier)
                else:
                    # Пробуем найти по имени
                    from database.whitelist import search_user_by_name
                    target_id = await search_user_by_name(identifier)
                    if not target_id:
                        await message.reply(
                            f"❌ Пользователь '{identifier}' не найден в базе данных\n\n"
                            "Используйте:\n"
                            "• ID: /wl -a 123456789\n"
                            "• Username: /wl -a @username\n"
                            "• Имя (если есть в БД): /wl -a Имя\n\n"
                            "Или расширенный формат для автодобавления:\n"
                            f"/wl -a @{identifier} ID Name",
                            parse_mode="HTML",
                            reply_markup=create_delete_button(message)
                        )
                        return
            
            # Проверка на бота
            try:
                u_chat = await bot.get_chat(target_id)
                if u_chat.is_bot:
                    await message.reply("❌ Ботов нельзя добавлять в whitelist", reply_markup=create_delete_button(message))
                    return
            except:
                pass

            # Проверяем, не в whitelist ли уже
            if WHITELIST_USERS and target_id in WHITELIST_USERS:
                await message.reply("❌ Этот пользователь уже в whitelist (в конфиге)", reply_markup=create_delete_button(message))
                return
            
            if target_id in await get_all_whitelist_users():
                await message.reply("❌ Этот пользователь уже в whitelist", reply_markup=create_delete_button(message))
                return
            
            await add_whitelist_user(target_id)

            # Получаем информацию для отображения
            try:
                user_info = await bot.get_chat(target_id)
                name = user_info.first_name or "Unknown"
                uname = f"(@{user_info.username})" if user_info.username else ""
            except:
                db_info = await get_user_info_from_db(target_id)
                if db_info:
                    username, name = db_info
                    uname = f"(@{username})" if username else ""
                else:
                    name = "Unknown"
                    uname = ""

            # Пытаемся отправить уведомление пользователю
            try:
                await bot.send_message(
                    target_id,
                    "✅ Вы добавлены в белый список бота!\n"
                    "Теперь вы можете использовать все функции бота.\n\n"
                    "✅ You have been added to the bot's whitelist!\n"
                    "You can now use all bot features.",
                    parse_mode="HTML"
                )
                logger.info(f"✅ Уведомление об добавлении в белый список отправлено пользователю {target_id}")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось отправить уведомление пользователю {target_id}: {e}")
            
            await message.reply(
                f"✅ Добавлен в whitelist:\n"
                f"👤 {name} {uname}\n"
                f"🆔 ID: {target_id}",
                parse_mode="HTML",
                reply_markup=create_delete_button(message)
            )
            return
        
        # /wl -r - удаление
        if args[1].lower() == '-r':
            if len(args) < 3:
                await message.reply(
                    "❌ Укажите ID или @username\n\n"
                    "Примеры:\n"
                    "• /wl -r 123456789\n"
                    "• /wl -r @username",
                    reply_markup=create_delete_button(message)
                )
                return
            
            identifier = args[2].strip()
            
            # Определяем user_id
            if identifier.startswith('@'):
                target_id = await get_user_id_by_username(identifier)
                if not target_id:
                    await message.reply(
                        f"❌ Пользователь {identifier} не найден в базе данных",
                        reply_markup=create_delete_button(message)
                    )
                    return
            elif identifier.isdigit():
                target_id = int(identifier)
            else:
                await message.reply(
                    "❌ Неверный формат. Используйте ID или @username",
                    reply_markup=create_delete_button(message)
                )
                return
            
            # Проверяем ограничения
            if WHITELIST_USERS and target_id in WHITELIST_USERS:
                await message.reply(
                    "❌ Этот пользователь в whitelist из конфига\n"
                    "Его можно удалить только из файла config/settings.py",
                    reply_markup=create_delete_button(message)
                )
                return
            
            if target_id not in await get_all_whitelist_users():
                await message.reply("❌ Этот пользователь не в whitelist", reply_markup=create_delete_button(message))
                return
            
            await remove_whitelist_user(target_id)

            # Получаем информацию для отображения
            try:
                user_info = await bot.get_chat(target_id)
                name = user_info.first_name or "Unknown"
                uname = f"(@{user_info.username})" if user_info.username else ""
            except:
                db_info = await get_user_info_from_db(target_id)
                if db_info:
                    username, name = db_info
                    uname = f"(@{username})" if username else ""
                else:
                    name = "Unknown"
                    uname = ""

            # Пытаемся отправить уведомление пользователю
            try:
                await bot.send_message(
                    target_id,
                    "❌ Вы удалены из белого списка бота.\n"
                    "Доступ к функциям бота ограничен.\n\n"
                    "❌ You have been removed from the bot's whitelist.\n"
                    "Access to bot features is restricted.",
                    parse_mode="HTML"
                )
                logger.info(f"✅ Уведомление об удалении из белого списка отправлено пользователю {target_id}")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось отправить уведомление пользователю {target_id}: {e}")
            
            await message.reply(
                f"✅ Удалён из whitelist:\n"
                f"👤 {name} {uname}\n"
                f"🆔 ID: {target_id}",
                parse_mode="HTML",
                reply_markup=create_delete_button(message)
            )
            return
        
        # Если аргумент — это ID или @username (без -a, -r, -e), показываем кнопки выбора
        identifier = args[1].strip()
        target_id = None
        display_name = identifier
        
        if identifier.startswith('@'):
            target_id = await get_user_id_by_username(identifier)
            if target_id:
                display_name = identifier
        elif identifier.isdigit():
            target_id = int(identifier)
            # Пробуем получить имя
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
            # Проверяем, в whitelist ли уже
            in_config = WHITELIST_USERS and target_id in WHITELIST_USERS
            in_db = target_id in await get_all_whitelist_users()
            in_whitelist = in_config or in_db
            
            buttons = []
            if in_whitelist:
                if not in_config:
                    buttons.append([InlineKeyboardButton(text="❌ Удалить из whitelist", callback_data=f"wl_action:remove:{target_id}")])
                status = "✅ В whitelist" + (" (конфиг)" if in_config else " (добавлен)")
            else:
                buttons.append([InlineKeyboardButton(text="✅ Добавить в whitelist", callback_data=f"wl_action:add:{target_id}")])
                status = "❌ Не в whitelist"
            
            buttons.append([InlineKeyboardButton(text="🗑 Закрыть", callback_data=f"delete_universal:{user_id}:{message.message_id}")])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await message.reply(
                f"👤 {display_name}\n"
                f"🆔 ID: {target_id}\n"
                f"📋 Статус: {status}\n\n"
                f"Выберите действие:",
                reply_markup=keyboard
            )
            return
        
        await message.reply(
            "❌ Неизвестная команда или пользователь не найден\n\n"
            "Используйте /wl help для справки",
            reply_markup=create_delete_button(message)
        )
    
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}", reply_markup=create_delete_button(message))
        logger.error(f"Ошибка в /wl: {e}")


@router.callback_query(F.data.startswith("wl_action:"))
async def wl_action_callback(callback: types.CallbackQuery, bot: Bot):
    """Обработчик inline-кнопок добавления/удаления из whitelist"""
    if callback.from_user.id not in PERMANENT_ADMIN:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    parts = callback.data.split(":")
    action = parts[1]  # add / remove
    target_id = int(parts[2])
    
    if action == "add":
        if target_id in await get_all_whitelist_users():
            await callback.answer("Уже в whitelist", show_alert=True)
            return

        await add_whitelist_user(target_id)

        # Пробуем уведомить пользователя
        try:
            await bot.send_message(
                target_id,
                "✅ Вы добавлены в белый список бота!\n"
                "Теперь вы можете использовать все функции бота.\n\n"
                "✅ You have been added to the bot's whitelist!\n"
                "You can now use all bot features."
            )
        except:
            pass

        await callback.answer("✅ Добавлен в whitelist")
        
        # Обновляем сообщение
        try:
            await callback.message.edit_text(
                f"✅ Пользователь {target_id} добавлен в whitelist",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑 Закрыть", callback_data=f"delete_universal:{callback.from_user.id}:0")]
                ])
            )
        except:
            pass
    
    elif action == "remove":
        if target_id not in await get_all_whitelist_users():
            await callback.answer("Не в whitelist", show_alert=True)
            return

        if WHITELIST_USERS and target_id in WHITELIST_USERS:
            await callback.answer("❌ Нельзя удалить — в конфиге", show_alert=True)
            return

        await remove_whitelist_user(target_id)

        # Пробуем уведомить пользователя
        try:
            await bot.send_message(
                target_id,
                "❌ Вы удалены из белого списка бота.\n"
                "Доступ к функциям бота ограничен.\n\n"
                "❌ You have been removed from the bot's whitelist.\n"
                "Access to bot features is restricted."
            )
        except:
            pass

        await callback.answer("✅ Удалён из whitelist")
        
        try:
            await callback.message.edit_text(
                f"❌ Пользователь {target_id} удалён из whitelist",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑 Закрыть", callback_data=f"delete_universal:{callback.from_user.id}:0")]
                ])
            )
        except:
            pass
