from aiogram import Router, types, Bot, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command

from config.settings import PERMANENT_ADMIN, logger
from database.admins import is_admin, add_admin, remove_admin, get_all_admins
from database.whitelist import get_user_id_by_username, get_user_info_from_db, add_user_to_directory, set_pending_edit
from utils.helpers import create_delete_button


router = Router()


@router.message(Command('admin'))
async def admin_list_command(message: types.Message, bot: Bot):
    """Управление списком админов
    
    Команды:
    /admin - показать список админов
    /admin -a <ID|@username> - добавить админа
    /admin -r <ID|@username> - удалить админа
    """
    if message.from_user.id not in PERMANENT_ADMIN:
        return
    
    user_id = message.from_user.id
    
    try:
        args = message.text.split()
        
        # /admin без аргументов - показать список
        if len(args) == 1:
            db_admins = await get_all_admins()
            
            text = "👑 Список администраторов:\n"
            
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
            
            # Собираем список админов (БЕЗ PERMANENT_ADMIN - он скрыт)
            all_admins_list = []
            
            # PERMANENT_ADMIN
            if PERMANENT_ADMIN:
                all_admins_list.extend(PERMANENT_ADMIN)
            
            # Админы из БД (исключая PERMANENT_ADMIN)
            for admin_id in db_admins:
                if  admin_id not in PERMANENT_ADMIN and admin_id not in all_admins_list:
                    all_admins_list.append(admin_id)
            
            for admin_id in all_admins_list:
                text += f"  • {await format_user(admin_id)}\n"
            
            text += "\n💡 /admin help - справка"
            
            await message.reply(text, parse_mode="HTML", reply_markup=create_delete_button(message))
            return
        
        # /admin help - справка
        if args[1].lower() == 'help':
            await message.reply(
                "📖 Управление администраторами:\n\n"
                "Просмотр:\n"
                "• /admin - показать всех админов\n\n"
                "Добавление:\n"
                "• /admin -a 123456789 - добавить по ID\n"
                "• /admin -a @username - добавить по username\n"
                "• /admin -a @username 123456789 - добавить с автодобавлением в БД\n"
                "• /admin -a @username 123456789 Name - с указанием имени\n\n"
                "Редактирование:\n"
                "• /admin -e 123456789 - редактировать по ID\n"
                "• /admin -e @username - редактировать по username\n\n"
                "Удаление:\n"
                "• /admin -r 123456789 - удалить по ID\n"
                "• /admin -r @username - удалить по username\n\n",
                parse_mode="HTML",
                reply_markup=create_delete_button(message)
            )
            return
        
        # /admin -e - редактирование
        if args[1].lower() == '-e':
            if len(args) < 3:
                await message.reply(
                    "❌ Укажите ID или @username\n\n"
                    "Примеры:\n"
                    "• /admin -e 123456789\n"
                    "• /admin -e @username",
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
            
            # Проверяем что пользователь является админом
            all_admins = list(await get_all_admins())
            if PERMANENT_ADMIN:
                all_admins.extend(PERMANENT_ADMIN)
            # PERMANENT_ADMIN тоже админ, но скрыт от списков
            
            
            if target_id not in all_admins:
                await message.reply(
                    f"❌ Пользователь с ID {target_id} не является админом",
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
            set_pending_edit(user_id, 'admin', target_id)
            
            await message.reply(
                f"📝 Редактирование админа:\n\n"
                f"Текущие данные: {current_info}\n\n"
                f"Отправьте новые данные в формате:\n"
                f"@username {target_id} Имя\n\n"
                f"⚠️ ID изменить нельзя",
                parse_mode="HTML",
                reply_markup=create_delete_button(message)
            )
            return
        
        # /admin -a - добавление
        if args[1].lower() == '-a':
            if len(args) < 3:
                await message.reply(
                    "❌ Укажите ID или @username\n\n"
                    "Примеры:\n"
                    "• /admin -a 123456789\n"
                    "• /admin -a @username\n"
                    "• /admin -a @username 123456789 - автодобавление в БД",
                    reply_markup=create_delete_button(message)
                )
                return
            
            identifier = args[2].strip()
            username_for_db = None
            first_name_for_db = None
            
            # Если указано 3+ аргумента - это добавление с автодобавлением в БД
            # Формат: /admin -a @username ID [Name]
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
                            f"/admin -a {identifier} ID\n"
                            f"/admin -a {identifier} ID Name",
                            parse_mode="HTML",
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
            
            # Проверка на бота
            try:
                u_chat = await bot.get_chat(target_id)
                if u_chat.is_bot:
                    await message.reply("❌ Ботов нельзя назначать администраторами", reply_markup=create_delete_button(message))
                    return
            except:
                pass

            # Проверяем, не является ли уже админом
            if target_id in PERMANENT_ADMIN :
                await message.reply("❌ Этот пользователь уже постоянный админ", reply_markup=create_delete_button(message))
                return
            
            if target_id in await get_all_admins():
                await message.reply("❌ Этот пользователь уже админ", reply_markup=create_delete_button(message))
                return
            
            await add_admin(target_id)
            
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
            
            await message.reply(
                f"✅ Админ добавлен:\n"
                f"👤 {name} {uname}\n"
                f"🆔 ID: {target_id}",
                parse_mode="HTML",
                reply_markup=create_delete_button(message)
            )
            return
        
        # /admin -r - удаление
        if args[1].lower() == '-r':
            if len(args) < 3:
                await message.reply(
                    "❌ Укажите ID или @username\n\n"
                    "Примеры:\n"
                    "• /admin -r 123456789\n"
                    "• /admin -r @username",
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
            if target_id in PERMANENT_ADMIN :
                await message.reply("❌ Нельзя удалить постоянного админа", reply_markup=create_delete_button(message))
                return
            
            if target_id not in await get_all_admins():
                await message.reply("❌ Этот пользователь не является админом", reply_markup=create_delete_button(message))
                return
            
            await remove_admin(target_id)
            
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
            
            await message.reply(
                f"✅ Админ удалён:\n"
                f"👤 {name} {uname}\n"
                f"🆔 ID: {target_id}",
                parse_mode="HTML",
                reply_markup=create_delete_button(message)
            )
            return
        
        # /admin <id> или /admin @username — показать информацию с кнопками
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
            # Проверяем статус админа
            is_permanent = target_id in PERMANENT_ADMIN 
            is_db_admin = target_id in await get_all_admins()
            is_admin_user = is_permanent or is_db_admin
            
            buttons = []
            if is_admin_user:
                if not is_permanent:
                    buttons.append([InlineKeyboardButton(text="❌ Удалить из админов", callback_data=f"admin_action:remove:{target_id}")])
                status = "✅ Админ" + (" (постоянный)" if is_permanent else " (добавлен)")
            else:
                buttons.append([InlineKeyboardButton(text="✅ Добавить как админа", callback_data=f"admin_action:add:{target_id}")])
                status = "❌ Не админ"
            
            buttons.append([InlineKeyboardButton(text="🗑️ Закрыть", callback_data=f"delete_universal:{user_id}:{message.message_id}")])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await message.reply(
                f"👤 {display_name}\n"
                f"🆔 ID: {target_id}\n"
                f"👑 Статус: {status}\n\n"
                f"Выберите действие:",
                reply_markup=keyboard
            )
            return
        
        await message.reply(
            "❌ Неизвестная команда\n\n"
            "Используйте /admin help для справки",
            reply_markup=create_delete_button(message)
        )
    
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}", reply_markup=create_delete_button(message))
        logger.error(f"Ошибка в /admin: {e}")


@router.callback_query(F.data.startswith("admin_action:"))
async def admin_action_callback(callback: types.CallbackQuery, bot: Bot):
    """Обработчик inline-кнопок добавления/удаления админа"""
    if callback.from_user.id not in PERMANENT_ADMIN:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    parts = callback.data.split(":")
    action = parts[1]
    target_id = int(parts[2])
    
    if action == "add":
        if target_id in await get_all_admins() or target_id in PERMANENT_ADMIN :
            await callback.answer("Уже админ", show_alert=True)
            return
        
        await add_admin(target_id)
        await callback.answer("✅ Добавлен как админ")
        
        try:
            await callback.message.edit_text(
                f"✅ Пользователь {target_id} добавлен как админ",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑️ Закрыть", callback_data=f"delete_universal:{callback.from_user.id}:0")]
                ])
            )
        except:
            pass
    
    elif action == "remove":
        if target_id in PERMANENT_ADMIN :
            await callback.answer("❌ Нельзя удалить постоянного админа", show_alert=True)
            return
        
        if target_id not in await get_all_admins():
            await callback.answer("Не является админом", show_alert=True)
            return
        
        await remove_admin(target_id)
        await callback.answer("✅ Удалён из админов")
        
        try:
            await callback.message.edit_text(
                f"❌ Пользователь {target_id} удалён из админов",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑️ Закрыть", callback_data=f"delete_universal:{callback.from_user.id}:0")]
                ])
            )
        except:
            pass