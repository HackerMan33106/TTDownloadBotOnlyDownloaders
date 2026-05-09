"""
Обработчики callback-кнопок
"""

import os
import time
import shutil
import hashlib
import asyncio
import tempfile
import traceback
from pathlib import Path
from aiogram import Router, types, F, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.exceptions import TelegramBadRequest
from config.settings import logger, PERMANENT_ADMIN, COOKIES_PATH, DEBUG_MODE, MAX_UPLOAD_SIZE_MB, TRASH_GROUP_ID
from database.admins import is_admin
from database.audio import audio_url_storage, save_audio_url_storage, audio_downloaded, save_audio_downloaded, delete_audio_downloaded
from database.db import get_media_cache, delete_media_cache, delete_slideshow_cache, get_slideshow_cache
from utils.crypto import verify_callback, secure_callback
from utils.helpers import get_user_link, get_random_deny_message, get_disk_usage, get_temp_dir_size, create_media_caption
from utils.tiktok import extract_all_tiktok_urls, clean_tiktok_url
from services.downloader import download_video_sync, download_slideshow_sync
from handlers.commands.download_video import clean_url, find_downloader

router = Router()


@router.callback_query(F.data.startswith(("delete_universal", "delete_ping", "delete_video", "delete_slideshow", "delete_aiogram", "delete_error", "delete_storage", "delete_bl_msg", "delete_check", "delete_cookies", "delete_help_msg", "delete_stats_msg", "delete_message", "delete_parts", "show_cookies_copy", "extract_audio", "delete_audio", "send_slideshow_audio", "send_cached_audio", "dl_audio", "cleanup_temp", "explain_error", "clear_cache", "storage_auto")))
async def button_callback(callback: types.CallbackQuery, bot: Bot):
    """Обработчик всех callback-кнопок"""
    callback_data = callback.data.split(":")
    action = callback_data[0]
    current_user_id = callback.from_user.id

    # Обработчик удаления из кэша
    if action == "clear_cache":
        if callback.message.chat.id != TRASH_GROUP_ID:
            await callback.answer("❌ Кэш", show_alert=True)
            return

        try:
            url_hash = callback_data[1] if len(callback_data) > 1 else ""

            # Проверяем есть ли это в обычном кэше или в кэше слайдшоу
            cached = await get_media_cache(url_hash)

            # Если не нашли в обычном кэше, ищем по caption (для слайдшоу)
            if not cached and callback.message.caption:
                slideshow_url = callback.message.caption.strip()
                cached_slideshow = await get_slideshow_cache(slideshow_url)

                if cached_slideshow:
                    photo_ids, audio_id = cached_slideshow
                    expected_messages = len(photo_ids) + (1 if audio_id else 0)

                    # Удаляем кэш слайдшоу из базы
                    await delete_slideshow_cache(slideshow_url)

                    # Удаляем текущее сообщение (фото с кнопкой или аудио)
                    current_msg_id = callback.message.message_id
                    try:
                        await callback.message.delete()
                    except:
                        pass

                    # Ищем и удаляем связанные сообщения слайдшоу
                    # Ограничиваем поиск: expected_messages * 2 для подстраховки, но не более 50
                    max_search = min(expected_messages * 2, 50)

                    try:
                        deleted_count = 0
                        # Ищем только назад (слайдшоу обычно идёт последовательно)
                        for offset in range(1, max_search + 1):
                            if deleted_count >= expected_messages - 1:  # -1 потому что текущее уже удалили
                                break

                            msg_id = current_msg_id - offset
                            try:
                                # Пробуем удалить сообщение напрямую
                                await bot.delete_message(callback.message.chat.id, msg_id)
                                deleted_count += 1
                                logger.info(f"🗑️ Удалено сообщение слайдшоу из кэша (message_id: {msg_id})")

                                # Небольшая задержка чтобы избежать flood control
                                if deleted_count % 10 == 0:
                                    await asyncio.sleep(0.5)
                            except:
                                # Сообщение не найдено или уже удалено - продолжаем
                                pass

                        logger.info(f"🗑️ Админ {current_user_id} удалил кэш слайдшоу (URL: {slideshow_url}, удалено {deleted_count + 1} сообщений)")
                    except Exception as e:
                        logger.error(f"❌ Ошибка удаления связанных сообщений слайдшоу: {e}")

                    await callback.answer("✅ Кэш слайдшоу удалён", show_alert=False)
                    return

            if not cached:
                await callback.answer("❌ Кэш не найден", show_alert=True)
                return

            # Получаем URL из caption текущего сообщения
            cache_url = callback.message.caption.strip() if callback.message.caption else None

            # Удаляем из базы данных
            await delete_media_cache(url_hash)

            # Удаляем текущее сообщение (видео или аудио)
            current_msg_id = callback.message.message_id
            try:
                await callback.message.delete()
            except:
                pass

            # Ищем и удаляем связанное сообщение (если удалили видео - ищем аудио, если аудио - ищем видео)
            if cache_url:
                try:
                    # Проверяем последние 50 сообщений до и после текущего
                    deleted_related = False

                    for offset in range(1, 51):
                        if deleted_related:
                            break
                        for msg_id in [current_msg_id - offset, current_msg_id + offset]:
                            try:
                                # Форвардим сообщение чтобы получить его данные
                                msg_info = await bot.forward_message(
                                    chat_id=callback.message.chat.id,
                                    from_chat_id=callback.message.chat.id,
                                    message_id=msg_id
                                )

                                # Проверяем caption
                                has_matching_caption = msg_info.caption and msg_info.caption.strip() == cache_url

                                # Удаляем форвард
                                await bot.delete_message(callback.message.chat.id, msg_info.message_id)

                                # Если caption совпадает - удаляем оригинал
                                if has_matching_caption:
                                    await bot.delete_message(callback.message.chat.id, msg_id)
                                    if DEBUG_MODE:
                                        logger.info(f"🗑️ Удалено связанное сообщение из кэша (message_id: {msg_id})")
                                    deleted_related = True
                                    break
                            except:
                                pass

                    if not deleted_related:
                        logger.info(f"ℹ️ Связанное сообщение не найдено или уже удалено")
                except Exception as e:
                    logger.error(f"❌ Ошибка поиска связанного сообщения: {e}")

            logger.info(f"🗑️ Админ {current_user_id} удалил кэш через кнопку (hash: {url_hash})")
            try:
                await callback.answer("✅ Кэш удалён", show_alert=False)
            except:
                pass  # Игнорируем ошибку устаревшего callback

        except Exception as e:
            logger.error(f"❌ Ошибка удаления кэша через кнопку: {e}")
            try:
                await callback.answer("❌ Ошибка удаления", show_alert=True)
            except:
                pass  # Игнорируем ошибку устаревшего callback
        return

    # Универсальный обработчик удаления
    if action == "delete_universal":
        try:
            # Формат: delete_universal:user_id:command_message_id
            if len(callback_data) > 1:
                button_user_id = int(callback_data[1])
                if current_user_id == button_user_id or await is_admin(current_user_id):
                    # Извлекаем информацию из сообщения для логирования
                    message_id = callback.message.message_id
                    caption = callback.message.caption or callback.message.text or ""

                    # Парсим username и URL из caption
                    lines = caption.split('\n')
                    author_username = None
                    video_url = None

                    for line in lines:
                        if line.startswith('@'):
                            author_username = line.strip()
                        elif line.startswith('http'):
                            video_url = line.strip()

                    # Формируем username удаляющего пользователя
                    if callback.from_user.username:
                        deleter_info = f"@{callback.from_user.username} ({current_user_id})"
                    else:
                        deleter_info = str(current_user_id)

                    # Логируем с полной информацией
                    log_parts = [f"🗑️ Пользователь {deleter_info} удалил сообщение через кнопку"]
                    log_parts.append(f"ID сообщения: {message_id}")
                    if author_username:
                        # Пытаемся найти ID автора из button_user_id
                        author_info = f"{author_username} ({button_user_id})"
                        log_parts.append(f"Автор контента: {author_info}")
                    if video_url:
                        log_parts.append(f"URL: {video_url}")

                    logger.info(" | ".join(log_parts))

                    try:
                        await callback.message.delete()
                    except Exception:
                        pass
                    if len(callback_data) > 2:
                        try:
                            await bot.delete_message(callback.message.chat.id, int(callback_data[2]))
                        except Exception:
                            pass
                    await callback.answer("✅ Удалено", show_alert=False)
                else:
                    logger.warning(f"🚫 Пользователь {current_user_id} попытался удалить чужое сообщение (владелец: {button_user_id})")
                    await callback.answer("❌ Вы не можете удалить это сообщение", show_alert=True)
            else:
                await callback.answer("❌ Ошибка в данных кнопки", show_alert=True)
        except Exception as e:
            await callback.answer("❌ Не удалось удалить", show_alert=True)
            logger.error(f"Ошибка удаления через delete_universal: {e}")
        return
    
    if action in ["delete_ping", "delete_aiogram", "delete_storage", "delete_check", "delete_cookies", "delete_help_msg", "delete_stats_msg", "delete_message"]:
        try:
            await callback.message.delete()
            await callback.answer("✅ Удалено", show_alert=False)
            # Формат: action:original_message_id
            if len(callback_data) > 1:
                try:
                    await bot.delete_message(callback.message.chat.id, int(callback_data[1]))
                except Exception:
                    pass
        except Exception:
            await callback.answer("❌ Не удалось удалить", show_alert=True)
    
    elif action == "delete_parts":
        # Формат: delete_parts:msg_id1,msg_id2,msg_id3 или delete_parts:msg_id1,msg_id2,msg_id3:last
        try:
            if DEBUG_MODE:
                logger.info(f"🗑 delete_parts вызван: callback_data={callback_data}, len={len(callback_data)}")
            
            if len(callback_data) > 1:
                msg_ids = callback_data[1].split(',')
                if DEBUG_MODE:
                    logger.info(f"📋 Список ID для удаления: {msg_ids}")
                
                # Удаляем все части
                deleted_count = 0
                for msg_id in msg_ids:
                    try:
                        await bot.delete_message(callback.message.chat.id, int(msg_id))
                        deleted_count += 1
                        if DEBUG_MODE:
                            logger.info(f"✅ Удалено сообщение {msg_id}")
                    except Exception as e:
                        logger.error(f"❌ Не удалось удалить сообщение {msg_id}: {e}")
                        pass
                
                if DEBUG_MODE:
                    logger.info(f"📊 Удалено частей: {deleted_count}/{len(msg_ids)}")
            
            # Проверяем есть ли флаг :last (означает что это последняя часть с кнопкой)
            if len(callback_data) > 2 and callback_data[2] == "last":
                # Удаляем текущее сообщение (последнюю часть с кнопкой)
                try:
                    await callback.message.delete()
                    if DEBUG_MODE:
                        logger.info(f"✅ Удалено последнее сообщение (текущее)")
                except Exception as e:
                    logger.error(f"❌ Не удалось удалить последнее сообщение: {e}")
            else:
                if DEBUG_MODE:
                    logger.info(f"⚠️ Флаг :last НЕ найден, текущее сообщение не удаляется")
            
            await callback.answer("✅ Все части удалены", show_alert=False)
        except Exception as e:
            logger.error(f"❌ Ошибка удаления частей: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            await callback.answer("❌ Не удалось удалить", show_alert=True)
    
    elif action == "delete_bl_msg":
        try:
            if len(callback_data) > 1:
                button_user_id = int(callback_data[1])
                if current_user_id == button_user_id or await is_admin(current_user_id):
                    await callback.message.delete()
                    if len(callback_data) > 2:
                        try:
                            await bot.delete_message(callback.message.chat.id, int(callback_data[2]))
                        except Exception:
                            pass
                    await callback.answer("✅ Удалено", show_alert=False)
                else:
                    await callback.answer("❌ Вы не можете удалить это сообщение", show_alert=True)
            else:
                await callback.message.delete()
                await callback.answer("✅ Удалено", show_alert=False)
        except Exception:
            await callback.answer("❌ Не удалось удалить", show_alert=True)
    
    elif action in ["delete_admin_msg", "delete_wl_msg"]:
        try:
            if len(callback_data) > 1:
                button_user_id = int(callback_data[1])
                if current_user_id == button_user_id or await is_admin(current_user_id):
                    await callback.message.delete()
                    if len(callback_data) > 2:
                        try:
                            await bot.delete_message(callback.message.chat.id, int(callback_data[2]))
                        except Exception:
                            pass
                    await callback.answer("✅ Удалено", show_alert=False)
                else:
                    await callback.answer("❌ Вы не можете удалить это сообщение", show_alert=True)
            else:
                await callback.message.delete()
                await callback.answer("✅ Удалено", show_alert=False)
        except Exception:
            await callback.answer("❌ Не удалось удалить", show_alert=True)
    
    elif action == "show_cookies_copy":
        logger.info(f"📋 Запрос на копирование cookies от {current_user_id}")
        try:
            if current_user_id not in PERMANENT_ADMIN:
                await callback.answer("❌ Нет доступа", show_alert=True)
                return
            await callback.answer("⏳ Загружаю...", show_alert=False)
            
            if len(callback_data) > 1:
                button_user_id = int(callback_data[1])
                if current_user_id != button_user_id:
                    await callback.answer("❌ Нет доступа", show_alert=True)
                    return
            
            if os.path.exists(COOKIES_PATH):
                with open(COOKIES_PATH, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                logger.info(f"📋 Отправка cookies ({len(content)} символов)")
                
                if len(content) > 3500:
                    input_file = FSInputFile(COOKIES_PATH, filename='cookies_copy.txt')
                    await callback.message.reply_document(
                        document=input_file,
                        caption="📋 Содержимое cookies.txt для копирования"
                    )
                else:
                    await callback.message.reply(
                        f"📋 Содержимое cookies.txt для копирования:\n\n```\n{content}\n```",
                        parse_mode="Markdown"
                    )
                
                await callback.answer("✅ Отправлено", show_alert=False)
            else:
                await callback.answer("❌ Файл не найден", show_alert=True)
        except Exception as e:
            logger.error(f"❌ Ошибка в show_cookies_copy: {e}")
            await callback.answer(f"❌ Ошибка: {str(e)[:100]}", show_alert=True)
    
    elif action == "cleanup_temp":
        if not await is_admin(callback.from_user.id):
            await callback.answer("❌ Нет доступа", show_alert=True)
            return
        
        try:
            await callback.answer("🧹 Очистка...", show_alert=False)
            
            cleanup_old_temp_files()
            
            # Очищаем журналы systemd (только на Linux с правами sudo)
            try:
                if platform.system() == "Linux":
                    process = await asyncio.create_subprocess_exec(
                        "sudo", "-n", "journalctl", "--vacuum-size=200M",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await process.communicate()
                    if process.returncode == 0:
                        logger.info("🧹 Журналы systemd очищены (лимит 200MB)")
            except (FileNotFoundError, PermissionError):
                logger.info("ℹ️ Пропущена очистка журналов systemd (нет sudo или прав)")
            except Exception:
                pass
            
            disk = get_disk_usage()
            temp_size, temp_files = get_temp_dir_size()
            temp_mb = temp_size / (1024 * 1024)
            
            text = "✅ Очистка завершена!\n\n📊 Текущее состояние:\n\n"
            
            if disk:
                text += f"💾 Диск:\n"
                text += f"  Свободно: {disk['free_gb']:.1f} GB ({100 - disk['used_percent']:.1f}%)\n\n"
            
            text += f"📁 Временные файлы:\n"
            text += f"  Размер: {temp_mb:.1f} MB\n"
            text += f"  Файлов: {temp_files}\n"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_storage:{callback.message.message_id}")]
            ])
            
            await callback.message.edit_text(text, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"❌ Ошибка очистки: {e}")
            await callback.answer("❌ Ошибка очистки", show_alert=True)
    
    elif action == "send_cached_audio":
        hash_val = callback_data[1] if len(callback_data) > 1 else ""
        cached = await get_media_cache(hash_val)

        if not cached or not cached[1]:
            await callback.answer("❌ Кэш не найден", show_alert=True)
            return

        audio_file_id = cached[1]

        try:
            user = callback.from_user

            # Получаем информацию о видео из caption
            video_title = ""
            if callback.message.caption:
                lines = callback.message.caption.split('\n')
                for line in lines:
                    if line.startswith('http'):
                        continue
                    if '@' in line or 'осталось' in line:
                        continue
                    video_title = line.strip()
                    break

            # Используем универсальную функцию для создания caption (только username, без названия)
            caption = create_media_caption(
                user,
                media_type="audio",
                title=None,
                audio_from_button=False
            )

            # Создаем уникальный ID для аудио
            audio_id = f"cached_audio_{user.id}_{int(time.time())}"

            # Создаем кнопки: "Оригинальное видео" и "Удалить"
            chat_id = callback.message.chat.id
            video_message_id = callback.message.message_id

            buttons = []
            # Добавляем кнопку "Оригинальное видео" только для групп
            if str(chat_id).startswith("-100"):
                clean_chat_id = str(chat_id)[4:]
                video_link = f"https://t.me/c/{clean_chat_id}/{video_message_id}"
                buttons.append([InlineKeyboardButton(text="📹 Оригинальное видео", url=video_link)])

            buttons.append([InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{user.id}:{audio_id}"))])
            audio_keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            # Получаем название для аудио
            audio_title = video_title if video_title else "Аудио"

            sent_audio = await bot.send_audio(
                chat_id=chat_id,
                audio=audio_file_id,
                caption=caption,
                title="",
                parse_mode="HTML",
                reply_markup=audio_keyboard
            )

            # Сохраняем информацию об отправленном аудио
            audio_data = {
                "message_id": sent_audio.message_id,
                "chat_id": chat_id,
                "file_id": audio_file_id,
                "video_message_id": video_message_id,
                "url_hash": hash_val
            }
            audio_downloaded[audio_id] = audio_data
            await save_audio_downloaded(audio_id, audio_data)

            # Изменяем кнопку на "Аудио" со ссылкой
            if str(chat_id).startswith("-100"):
                clean_chat_id = str(chat_id)[4:]
                audio_link = f"https://t.me/c/{clean_chat_id}/{sent_audio.message_id}"

                new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎵 Аудио", url=audio_link)],
                    [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_video:{user.id}")]
                ])

                await callback.message.edit_reply_markup(reply_markup=new_keyboard)

            await callback.answer("✅ Аудио отправлено", show_alert=False)

        except TelegramBadRequest as e:
            logger.warning(f"⚠️ Кэш устарел (TelegramBadRequest): {hash_val} - {e}")
            await delete_media_cache(hash_val)

            # Изменяем кнопку обратно на "📥 Установленное аудио"
            try:
                new_kb = []
                if callback.message.reply_markup and callback.message.reply_markup.inline_keyboard:
                    for row in callback.message.reply_markup.inline_keyboard:
                        new_row = []
                        for btn in row:
                            if btn.callback_data:
                                real_bd = verify_callback(btn.callback_data)
                                if real_bd and real_bd.startswith("send_cached_audio"):
                                    url_id = f"{current_user_id}_{int(time.time())}"
                                    new_row.append(InlineKeyboardButton(text="📥 Установленное аудио", callback_data=f"extract_audio:{url_id}"))
                                else:
                                    new_row.append(btn)
                            else:
                                new_row.append(btn)
                        if new_row:
                            new_kb.append(new_row)

                await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=new_kb))
            except Exception as edit_err:
                logger.error(f"❌ Не удалось обновить кнопку: {edit_err}")

            await callback.answer("Кэш устарел, нажмите 'Установленное аудио' еще раз", show_alert=True)

        except Exception as e:
            logger.error(f"❌ Ошибка отправки кэшированного аудио: {e}")
            await callback.answer("❌ Ошибка отправки", show_alert=True)
                
    elif action == "explain_error":
        # Формат: explain_error:KEY  (KEY — 8-символьный hex из utils.ai_error.store_error)
        try:
            key = callback_data[1] if len(callback_data) > 1 else ""
            stored = None

            if stored is None:
                await callback.answer("❌ Контекст ошибки устарел или не найден", show_alert=True)
                return

            original_user_id, err_ctx, err_url, log_ctx = stored

            if original_user_id and current_user_id != original_user_id and not await is_admin(current_user_id):
                await callback.answer("❌ Информация только для автора запроса", show_alert=True)
                return

            await callback.answer("⏳ Спрашиваю ИИ...", show_alert=False)

            explanation = await asyncio.get_event_loop().run_in_executor(
                None, get_ai_explanation, err_ctx, err_url, log_ctx
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_error:{original_user_id}")]
            ])
            await callback.message.reply(explanation, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"❌ explain_error: {e}")
            await callback.answer("❌ Не удалось получить объяснение", show_alert=True)

    elif action == "delete_error":
        if len(callback_data) > 1:
            try:
                original_user_id = int(callback_data[1])
                if await is_admin(current_user_id) or current_user_id == original_user_id:
                    try:
                        await callback.message.delete()
                        await callback.answer("✅ Ошибка удалена", show_alert=False)
                        logger.info(f"🗑️ Сообщение об ошибке удалено пользователем {current_user_id}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка удаления сообщения об ошибке: {e}")
                        await callback.answer("❌ Не удалось удалить", show_alert=True)
                else:
                    logger.warning(f"🚫 Попытка удаления без прав: пользователь {current_user_id}, автор {original_user_id}")
                    await callback.answer("❌ У вас нет прав для удаления этого сообщения", show_alert=True)
            except (ValueError, IndexError) as e:
                logger.error(f"❌ Ошибка парсинга callback_data для delete_error: {callback_data} - {e}")
                await callback.answer("❌ Ошибка при удалении", show_alert=True)
        else:
            logger.warning(f"❌ Недостаточно параметров в callback_data: {callback_data}")
            await callback.answer("❌ Ошибка при удалении", show_alert=True)
    
    elif action in ["delete_video", "delete_slideshow"]:
        if len(callback_data) > 1:
            original_user_id = int(callback_data[1])
            if await is_admin(current_user_id) or current_user_id == original_user_id:
                try:
                    if action == "delete_slideshow":
                        deleted_count = 0
                        
                        try:
                            await callback.message.delete()
                            deleted_count += 1
                        except Exception:
                            pass
                        
                        if len(callback_data) > 2:
                            slideshow_id = callback_data[2]
                            message_ids_str = None
                            
                            if slideshow_id in audio_url_storage:
                                storage_data = audio_url_storage[slideshow_id]
                                message_ids_str = storage_data.get("message_ids")
                                logger.info(f"🔍 Найден slideshow_id в storage: {slideshow_id}")
                            
                            if not message_ids_str:
                                for audio_id, data in audio_url_storage.items():
                                    if data.get("slideshow_id") == slideshow_id and data.get("type") == "slideshow_audio":
                                        message_ids_str = data.get("message_ids")
                                        logger.info(f"🔍 Найден slideshow_id в audio_id записи: {audio_id}")
                                        break
                            
                            if not message_ids_str and "," in slideshow_id:
                                message_ids_str = slideshow_id
                                logger.info(f"🔍 Используем старый формат с прямым списком ID")
                            
                            if message_ids_str:
                                message_ids = [int(msg_id) for msg_id in message_ids_str.split(",")]
                                logger.info(f"🗑️ Удаляем {len(message_ids)} сообщений медиа-группы")
                                
                                for msg_id in message_ids:
                                    try:
                                        await bot.delete_message(callback.message.chat.id, msg_id)
                                        deleted_count += 1
                                    except Exception as del_err:
                                        logger.warning(f"⚠️ Не удалось удалить сообщение {msg_id}: {del_err}")
                            else:
                                logger.error(f"❌ Не найдены message_ids для slideshow_id: {slideshow_id}")
                                await callback.answer("❌ Не удалось найти данные слайдшоу", show_alert=True)
                                return
                        
                        if deleted_count > 0:
                            if len(callback_data) > 2:
                                slideshow_id = callback_data[2]
                                
                                if slideshow_id in audio_url_storage:
                                    audio_url_storage[slideshow_id]["deleted"] = True
                                    await save_audio_url_storage(slideshow_id, audio_url_storage[slideshow_id])
                                
                                for audio_id, data in audio_url_storage.items():
                                    if data.get("slideshow_id") == slideshow_id:
                                        audio_url_storage[audio_id]["deleted"] = True
                                        await save_audio_url_storage(audio_id, audio_url_storage[audio_id])
                                        break

                                # Удаляем кнопку "Оригинальное слайдшоу" у всех связанных аудио
                                # Получаем ID последнего сообщения медиа-группы для сопоставления
                                last_msg_id = None
                                if message_ids:
                                    last_msg_id = message_ids[-1]

                                for audio_id, audio_data in list(audio_downloaded.items()):
                                    if audio_data.get("chat_id") == callback.message.chat.id:
                                        should_update = False

                                        # Проверяем связь через slideshow_id в audio_url_storage
                                        if audio_id in audio_url_storage:
                                            storage_data = audio_url_storage[audio_id]
                                            if storage_data.get("slideshow_id") == slideshow_id and storage_data.get("type") == "slideshow_audio":
                                                should_update = True
                                            # Также проверяем через last_media_group_message_id
                                            elif last_msg_id and storage_data.get("last_media_group_message_id") == last_msg_id:
                                                should_update = True

                                        if should_update:
                                            try:
                                                audio_msg_id = audio_data.get("message_id")
                                                if audio_msg_id:
                                                    # Создаем новую клавиатуру без кнопки "Оригинальное слайдшоу"
                                                    new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                                        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{original_user_id}:{audio_id}"))]
                                                    ])
                                                    await bot.edit_message_reply_markup(
                                                        chat_id=audio_data["chat_id"],
                                                        message_id=audio_msg_id,
                                                        reply_markup=new_keyboard
                                                    )
                                                    logger.info(f"✅ Удалена кнопка 'Оригинальное слайдшоу' у аудио {audio_id}")
                                            except TelegramBadRequest as e:
                                                # Игнорируем ошибки если сообщение уже удалено или недействительно
                                                error_msg = str(e).lower()
                                                if "message to edit not found" not in error_msg and "message_id_invalid" not in error_msg:
                                                    logger.warning(f"⚠️ Не удалось обновить кнопки аудио слайдшоу {audio_id}: {e}")
                                            except Exception as e:
                                                logger.warning(f"⚠️ Не удалось обновить кнопки аудио слайдшоу {audio_id}: {e}")

                            await callback.answer(f"✅ Удалено {deleted_count} фото", show_alert=False)
                            logger.info(f"✅ Слайдшоу удалено: {deleted_count} сообщений")
                    else:
                        # Удаляем видео
                        video_message_id = callback.message.message_id
                        await callback.message.delete()

                        # Удаляем кнопку "Оригинальное видео" у всех связанных аудио

                        for audio_id, audio_data in list(audio_downloaded.items()):
                            if audio_data.get("video_message_id") == video_message_id and audio_data.get("chat_id") == callback.message.chat.id:
                                try:
                                    # Получаем текущую клавиатуру аудио
                                    audio_msg_id = audio_data.get("message_id")
                                    if audio_msg_id:
                                        # Создаем новую клавиатуру без кнопки "Оригинальное видео"
                                        new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                            [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_audio:{original_user_id}:{audio_id}")]
                                        ])
                                        await bot.edit_message_reply_markup(
                                            chat_id=audio_data["chat_id"],
                                            message_id=audio_msg_id,
                                            reply_markup=new_keyboard
                                        )
                                except Exception as e:
                                    logger.warning(f"⚠️ Не удалось обновить кнопки аудио {audio_id}: {e}")
                except Exception as e:
                    logger.error(f"❌ Ошибка при удалении {action}: {e}")
            else:
                await callback.answer(get_random_deny_message(), show_alert=True)

    elif action == "delete_audio":
        if len(callback_data) > 1:
            original_user_id = int(callback_data[1])
            audio_id = callback_data[2] if len(callback_data) > 2 else None

            download_requester_id = None
            if audio_id and audio_id in audio_downloaded:
                download_requester_id = audio_downloaded[audio_id].get("download_requester_id")
            
            if await is_admin(current_user_id) or current_user_id == original_user_id or (download_requester_id and current_user_id == download_requester_id):
                try:
                    await callback.message.delete()
                    await callback.answer("✅ Удалено", show_alert=False)

                    if DEBUG_MODE:
                        logger.info(f"🗑️ delete_audio вызван: audio_id={audio_id}, есть в audio_downloaded={audio_id in audio_downloaded if audio_id else False}")
                    
                    if audio_id and audio_id in audio_downloaded:
                        audio_data = audio_downloaded[audio_id]
                        if DEBUG_MODE:
                            logger.info(f"💾 Удаляем аудио из БД: audio_id={audio_id}, audio_data={audio_data}")

                        # Проверяем, это кэшированное аудио или обычное
                        is_cached_audio = audio_id.startswith("cached_audio_")
                        url_hash = audio_data.get("url_hash")

                        del audio_downloaded[audio_id]
                        await delete_audio_downloaded(audio_id)
                        if DEBUG_MODE:
                            logger.info(f"✅ Аудио удалено из audio_downloaded")

                        # Восстанавливаем кнопку "Установленное аудио" для кэшированного аудио
                        if is_cached_audio and url_hash and "video_message_id" in audio_data:
                            try:
                                new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="🎵 Установленное аудио", callback_data=f"send_cached_audio:{url_hash}")],
                                    [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_video:{original_user_id}")]
                                ])
                                await bot.edit_message_reply_markup(
                                    chat_id=audio_data["chat_id"],
                                    message_id=audio_data["video_message_id"],
                                    reply_markup=new_keyboard
                                )
                                if DEBUG_MODE:
                                    logger.info(f"✅ Кнопка 'Установленное аудио' восстановлена")
                            except TelegramBadRequest as tg_err:
                                if "message to edit not found" in str(tg_err).lower():
                                    logger.debug(f"Сообщение с видео уже удалено, пропускаем восстановление кнопки")
                                else:
                                    logger.warning(f"⚠️ Не удалось восстановить кнопку 'Установленное аудио': {tg_err}")
                            except Exception as restore_err:
                                logger.warning(f"⚠️ Не удалось восстановить кнопку 'Установленное аудио': {restore_err}")
                            return

                        # Определяем тип видео: TikTok (есть в audio_url_storage) или /dw (есть video_message_id в audio_data)
                        is_tiktok = audio_id in audio_url_storage and "video_message_id" in audio_url_storage[audio_id]
                        is_dw_video = "video_message_id" in audio_data and not is_tiktok
                        
                        # Восстанавливаем кнопку для TikTok видео
                        if is_tiktok:
                            storage_data = audio_url_storage[audio_id]
                            try:
                                new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="🎵 Установленное аудио", callback_data=f"extract_audio:{audio_id}")],
                                    [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_video:{original_user_id}")]
                                ])
                                await bot.edit_message_reply_markup(
                                    chat_id=storage_data["chat_id"],
                                    message_id=storage_data["video_message_id"],
                                    reply_markup=new_keyboard
                                )
                                if DEBUG_MODE:
                                    logger.info(f"✅ Кнопка восстановлена для TikTok видео")
                            except Exception as e:
                                logger.warning(f"⚠️ Не удалось восстановить кнопку TikTok видео: {e}")
                        
                        # Восстанавливаем кнопку для /dw видео
                        elif is_dw_video:
                            try:
                                # Получаем URL из сохранённых данных
                                video_url = audio_data.get("video_url")
                                
                                # Получаем сохраненную информацию о кнопке delete_parts
                                saved_delete_callback = audio_data.get("delete_callback")
                                saved_delete_button_text = audio_data.get("delete_button_text")
                                
                                if DEBUG_MODE:
                                    logger.info(f"🔍 Из БД: delete_callback='{saved_delete_callback}', delete_button_text='{saved_delete_button_text}'")
                                
                                # Если URL не сохранён (старая запись), получаем через caption
                                if not video_url:
                                    if DEBUG_MODE:
                                        logger.warning(f"⚠️ video_url не найден в audio_data (старая запись), получаем через caption")
                                    try:
                                        # Используем forward для получения caption (для старых записей)
                                        forwarded = await bot.forward_message(
                                            chat_id=callback.message.chat.id,
                                            from_chat_id=audio_data["chat_id"],
                                            message_id=audio_data["video_message_id"]
                                        )
                                        if forwarded.caption:
                                            if DEBUG_MODE:
                                                logger.info(f"📝 Caption получен через forward: {forwarded.caption[:100]}")
                                            for word in forwarded.caption.split():
                                                if word.startswith('http'):
                                                    video_url = word
                                                    if DEBUG_MODE:
                                                        logger.info(f"🔗 Найден URL: {video_url}")
                                                    # Обновляем запись в БД с найденным URL
                                                    audio_data["video_url"] = video_url
                                                    await save_audio_downloaded(audio_id, audio_data)
                                                    break
                                        await forwarded.delete()
                                    except Exception as fwd_err:
                                        logger.warning(f"⚠️ Не удалось получить caption через forward: {fwd_err}")
                                
                                if video_url:
                                    if DEBUG_MODE:
                                        logger.info(f"🔗 Используем URL: {video_url}")
                                    short_url = video_url[:30] if len(video_url) > 30 else video_url
                                    
                                    # Используем сохраненную кнопку delete_parts или fallback на delete_message
                                    delete_callback = saved_delete_callback if saved_delete_callback else "delete_message"
                                    delete_button_text = saved_delete_button_text if saved_delete_button_text else "🗑 Удалить"
                                    
                                    if DEBUG_MODE:
                                        logger.info(f"🔧 Восстанавливаем кнопку удаления: text='{delete_button_text}', callback='{delete_callback}'")
                                    
                                    new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                        [InlineKeyboardButton(text="🎵 Установленное аудио", callback_data=f"dl_audio:{short_url}")],
                                        [InlineKeyboardButton(text=delete_button_text, callback_data=delete_callback)]
                                    ])
                                    await bot.edit_message_reply_markup(
                                        chat_id=audio_data["chat_id"],
                                        message_id=audio_data["video_message_id"],
                                        reply_markup=new_keyboard
                                    )
                                    if DEBUG_MODE:
                                        logger.info(f"✅ Кнопка восстановлена для /dw видео")
                                else:
                                    logger.warning(f"⚠️ URL не найден, кнопку невозможно восстановить")
                            except Exception as e:
                                logger.warning(f"⚠️ Не удалось восстановить кнопку /dw видео: {e}")
                                logger.error(f"Traceback: {traceback.format_exc()}")
                        
                        # Восстанавливаем кнопку для slideshow
                        if audio_id in audio_url_storage:
                            storage_data = audio_url_storage[audio_id]
                            if "control_message_id" in storage_data:
                                if not storage_data.get("deleted"):
                                    try:
                                        slideshow_id = storage_data.get("slideshow_id")
                                        if not slideshow_id:
                                            slideshow_id = f"slideshow_{original_user_id}_{int(time.time())}"
                                            storage_data["slideshow_id"] = slideshow_id
                                            audio_url_storage[audio_id] = storage_data
                                            await save_audio_url_storage(audio_id, storage_data)
                                            
                                            slideshow_data = {
                                                "message_ids": storage_data.get("message_ids", ""),
                                                "type": "slideshow"
                                            }
                                            audio_url_storage[slideshow_id] = slideshow_data
                                            await save_audio_url_storage(slideshow_id, slideshow_data)
                                        
                                        buttons = [
                                            [InlineKeyboardButton(text="🎵 Установленное аудио", callback_data=f"send_slideshow_audio:{audio_id}")],
                                            [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_slideshow:{original_user_id}:{slideshow_id}"))]
                                        ]
                                        
                                        new_keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
                                        await bot.edit_message_reply_markup(
                                            chat_id=storage_data["control_message_chat_id"],
                                            message_id=storage_data["control_message_id"],
                                            reply_markup=new_keyboard
                                        )
                                    except TelegramBadRequest as e:
                                        # Игнорируем ошибку если сообщение уже удалено
                                        if "message to edit not found" not in str(e).lower():
                                            logger.warning(f"⚠️ Не удалось восстановить кнопку слайдшоу: {e}")
                                    except Exception as e:
                                        logger.warning(f"⚠️ Не удалось восстановить кнопку слайдшоу: {e}")
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка при удалении аудио: {e}")
                    await callback.answer("❌ Не удалось удалить", show_alert=True)
            else:
                await callback.answer(get_random_deny_message(), show_alert=True)
    
    elif action == "extract_audio":
        if len(callback_data) > 1:
            url_id = callback_data[1]
            video_url = None
            original_user_id = None
            
            if url_id in audio_url_storage:
                storage_data = audio_url_storage[url_id]
                video_url = storage_data["url"] if isinstance(storage_data, dict) else storage_data
                original_user_id = int(url_id.split("_")[0])
            else:
                if callback.message.caption:
                    extracted_urls = extract_all_tiktok_urls(callback.message.caption)
                    if extracted_urls:
                        video_url = clean_tiktok_url(extracted_urls[0])
                        try:
                            original_user_id = int(url_id.split("_")[0])
                        except:
                            original_user_id = current_user_id
                        
                        if video_url:
                            logger.info(f"🔄 Восстановлен URL из caption: {video_url}")
                
                if not video_url:
                    await callback.answer("❌ Ссылка устарела и не найдена в сообщении", show_alert=True)
                    return
            
            if url_id in audio_downloaded:
                if not await is_admin(current_user_id):
                    audio_data = audio_downloaded[url_id]
                    chat_id = audio_data["chat_id"]
                    message_id = audio_data["message_id"]
                    
                    if str(chat_id).startswith("-100"):
                        clean_chat_id = str(chat_id)[4:]
                        audio_link = f"https://t.me/c/{clean_chat_id}/{message_id}"
                        await callback.answer(f"✅ Аудио уже было скачано", show_alert=True)
                    else:
                        await callback.answer("❌ Аудио уже было скачано", show_alert=True)
                    return
            
            await callback.answer("⏳ Отправляю аудио...", show_alert=False)
            
            temp_dir = os.path.join(tempfile.gettempdir(), "tiktok_bot")
            os.makedirs(temp_dir, exist_ok=True)
            video_path = None
            audio_path = None
            
            try:
                video_path = download_video_sync(video_url, attempt=1)
                
                if not video_path or not os.path.exists(video_path):
                    await callback.message.reply("❌ Не удалось скачать видео для извлечения аудио")
                    return
                
                audio_path = os.path.join(temp_dir, f"audio_{int(time.time())}.mp3")
                
                try:
                    process = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", audio_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    await process.communicate()
                    
                    if process.returncode != 0 or not os.path.exists(audio_path):
                        await callback.message.reply("❌ Ошибка при извлечении аудио")
                        return
                except FileNotFoundError:
                    logger.error("❌ ffmpeg не установлен! Установите: sudo apt install ffmpeg")
                    await callback.message.reply("❌ Сервер не настроен для извлечения аудио. Обратитесь к администратору.")
                    return
                
                username_display = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.first_name
                user_link = get_user_link(callback.from_user)

                # Создаем файл и клавиатуру после успешного извлечения
                audio_file = FSInputFile(audio_path)
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{original_user_id}:{url_id}"))]
                ])

                # Название для Аудио
                audio_title = "Аудио"

                sent_audio = await callback.message.reply_audio(
                    audio_file,
                    caption=f"{username_display}",
                    title=audio_title,
                    reply_markup=keyboard
                )
                
                downloaded_data = {
                    "message_id": sent_audio.message_id,
                    "chat_id": callback.message.chat.id
                }
                audio_downloaded[url_id] = downloaded_data
                await save_audio_downloaded(url_id, downloaded_data)

                if DEBUG_MODE:
                    logger.info(f"🔍 DEBUG: url_id={url_id}, есть ли в storage: {url_id in audio_url_storage}")
                
                if url_id in audio_url_storage:
                    storage_data = audio_url_storage[url_id]
                    if DEBUG_MODE:
                        logger.info(f"🔍 DEBUG: storage_data type={type(storage_data)}, keys={storage_data.keys() if isinstance(storage_data, dict) else 'not dict'}")
                        logger.info(f"🔍 DEBUG: video_message_id в storage: {'video_message_id' in storage_data if isinstance(storage_data, dict) else False}")
                    
                    if isinstance(storage_data, dict) and "video_message_id" in storage_data:
                        try:
                            chat_id = callback.message.chat.id
                            if DEBUG_MODE:
                                logger.info(f"🔍 DEBUG: chat_id={chat_id}, starts with -100: {str(chat_id).startswith('-100')}")
                            
                            if str(chat_id).startswith("-100"):
                                clean_chat_id = str(chat_id)[4:]
                                audio_link = f"https://t.me/c/{clean_chat_id}/{sent_audio.message_id}"
                                
                                if DEBUG_MODE:
                                    logger.info(f"🔍 DEBUG: Создана ссылка: {audio_link}")
                                    logger.info(f"🔍 DEBUG: Обновляем кнопку для chat_id={storage_data['chat_id']}, message_id={storage_data['video_message_id']}")
                                
                                new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="Установленное аудио", url=audio_link)],
                                    [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_video:{original_user_id}")]
                                ])
                                
                                await bot.edit_message_reply_markup(
                                    chat_id=storage_data["chat_id"],
                                    message_id=storage_data["video_message_id"],
                                    reply_markup=new_keyboard
                                )
                                if DEBUG_MODE:
                                    logger.info(f"✅ DEBUG: Кнопка успешно обновлена!")
                            else:
                                if DEBUG_MODE:
                                    logger.warning(f"⚠️ DEBUG: chat_id не начинается с -100, это личка - не обновляем кнопку")
                        except Exception as edit_error:
                            logger.error(f"❌ Не удалось обновить кнопку видео: {edit_error}")
                            if DEBUG_MODE:
                                logger.error(f"❌ DEBUG: Traceback: {traceback.format_exc()}")
                else:
                    if DEBUG_MODE:
                        logger.warning(f"⚠️ DEBUG: url_id={url_id} НЕ НАЙДЕН в audio_url_storage!")
                        logger.warning(f"⚠️ DEBUG: Доступные ключи в storage: {list(audio_url_storage.keys())[:10]}")
                
                if DEBUG_MODE:
                    logger.info(f"🎵 Аудио извлечено для {user_link} из {video_url}")
                
            except Exception as e:
                logger.error(f"❌ Ошибка при извлечении аудио: {e}")
                await callback.message.reply("❌ Ошибка при извлечении аудио")
            
            finally:
                if video_path and os.path.exists(video_path):
                    try:
                        os.remove(video_path)
                    except Exception:
                        pass
                if audio_path and os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except Exception:
                        pass
        else:
            await callback.answer("❌ Ошибка данных", show_alert=True)
    
    elif action == "send_slideshow_audio":
        if len(callback_data) > 1:
            audio_id = callback_data[1]
            audio_path = None
            slideshow_url = None
            
            if audio_id in audio_url_storage:
                storage_data = audio_url_storage[audio_id]

                if storage_data.get("type") != "slideshow_audio":
                    await callback.answer("❌ Неверный тип данных", show_alert=True)
                    return

                audio_path = storage_data.get("audio_path")
                audio_file_id_cached = storage_data.get("audio_file_id")
                slideshow_url = storage_data.get("slideshow_url")

                # Если есть закэшированный file_id, используем его
                if audio_file_id_cached:
                    logger.info(f"✅ Используем закэшированное аудио (file_id: {audio_file_id_cached})")
                elif audio_path and os.path.exists(audio_path):
                    logger.info(f"📁 Используем локальный файл: {audio_path}")
                elif slideshow_url:
                    audio_path = None
                else:
                    await callback.answer("❌ Аудио файл не найден", show_alert=True)
                    return
            else:
                try:
                    chat_id = callback.message.chat.id
                    current_msg_id = callback.message.message_id
                    
                    for offset in range(1, 15):
                        try:
                            prev_msg = await bot.copy_message(
                                chat_id=callback.from_user.id,
                                from_chat_id=chat_id,
                                message_id=current_msg_id - offset
                            )
                            await bot.delete_message(callback.from_user.id, prev_msg.message_id)
                            
                            try:
                                msg = await bot.forward_message(
                                    chat_id=callback.from_user.id,
                                    from_chat_id=chat_id,
                                    message_id=current_msg_id - offset
                                )
                                await bot.delete_message(callback.from_user.id, msg.message_id)
                                
                                if msg.caption:
                                    extracted_urls = extract_all_tiktok_urls(msg.caption)
                                    if extracted_urls:
                                        slideshow_url = clean_tiktok_url(extracted_urls[0])
                                        if slideshow_url:
                                            logger.info(f"🔄 URL восстановлен из медиа-группы")
                                            break
                            except Exception:
                                pass
                        except Exception:
                            continue
                
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось найти URL: {e}")
                
                if not slideshow_url:
                    await callback.answer("❌ Ссылка устарела и не найдена в сообщении", show_alert=True)
                    return
            
            try:
                await callback.answer("⏳ Отправляю аудио...", show_alert=False)

                # Если нет закэшированного file_id и нет локального файла, скачиваем
                if not audio_file_id_cached and (not audio_path or not os.path.exists(audio_path)):
                    if not slideshow_url:
                        await callback.answer("❌ Не удалось получить URL для скачивания", show_alert=True)
                        return

                    slideshow_files = await download_slideshow_sync(slideshow_url, attempt=1)

                    if not slideshow_files:
                        await callback.answer("❌ Не удалось скачать слайдшоу", show_alert=True)
                        return

                    for file_path in slideshow_files:
                        if file_path.lower().endswith(('.mp3', '.m4a', '.wav')):
                            size_mb = os.path.getsize(file_path) / (1024 * 1024)
                            if size_mb < 30:
                                audio_path = file_path
                                break

                    if not audio_path:
                        await callback.answer("❌ Аудио файл не найден в слайдшоу", show_alert=True)
                        for file_path in slideshow_files:
                            try:
                                os.remove(file_path)
                            except:
                                pass
                        return

                username_display = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.first_name

                try:
                    original_user_id = int(audio_id.split("_")[2])
                except:
                    original_user_id = current_user_id

                reply_to_message_id = None
                if audio_id in audio_url_storage:
                    storage_data = audio_url_storage[audio_id]
                    reply_to_message_id = storage_data.get("last_media_group_message_id")

                # Создаем кнопки с ссылкой на оригинальное слайдшоу
                buttons = []

                # Добавляем кнопку "Оригинальное слайдшоу" если это групповой чат
                if reply_to_message_id and str(callback.message.chat.id).startswith("-100"):
                    clean_chat_id = str(callback.message.chat.id)[4:]
                    slideshow_link = f"https://t.me/c/{clean_chat_id}/{reply_to_message_id}"
                    buttons.append([InlineKeyboardButton(text="🖼️ Оригинальное слайдшоу", url=slideshow_link)])

                buttons.append([InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{original_user_id}:{audio_id}"))])

                keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

                # Кэшируем аудио в мусорной группе если ещё не закэшировано
                if not audio_file_id_cached and audio_path and TRASH_GROUP_ID:
                    try:
                        logger.info(f"📦 Кэширование аудио слайдшоу в мусорную группу...")

                        # Создаём кнопку удаления для кэша
                        if slideshow_url:
                            url_hash = hashlib.md5(slideshow_url.encode()).hexdigest()[:16]
                            cache_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="🗑️ Удалить из кэша", callback_data=secure_callback(f"clear_cache:{url_hash}"))]
                            ])
                        else:
                            cache_keyboard = None

                        trash_audio = await bot.send_audio(
                            chat_id=TRASH_GROUP_ID,
                            audio=FSInputFile(audio_path),
                            title="Аудио",
                            caption=slideshow_url if slideshow_url else "Слайдшоу аудио",
                            reply_markup=cache_keyboard
                        )
                        audio_file_id_cached = trash_audio.audio.file_id
                        logger.info(f"✅ Аудио слайдшоу закэшировано")

                        # Обновляем storage с file_id
                        if audio_id in audio_url_storage:
                            storage_data = audio_url_storage[audio_id]
                            storage_data["audio_file_id"] = audio_file_id_cached
                            audio_url_storage[audio_id] = storage_data
                            await save_audio_url_storage(audio_id, storage_data)
                    except Exception as e:
                        logger.error(f"❌ Ошибка кэширования аудио слайдшоу: {e}")

                # Отправляем пользователю используя закэшированный file_id или файл
                if reply_to_message_id:
                    sent_audio = await bot.send_audio(
                        chat_id=callback.message.chat.id,
                        audio=audio_file_id_cached if audio_file_id_cached else FSInputFile(audio_path),
                        caption=f"{username_display}",
                        title="",
                        reply_markup=keyboard,
                        reply_to_message_id=reply_to_message_id
                    )
                else:
                    sent_audio = await bot.send_audio(
                        chat_id=callback.message.chat.id,
                        audio=audio_file_id_cached if audio_file_id_cached else FSInputFile(audio_path),
                        caption=f"{username_display}",
                        title="",
                        reply_markup=keyboard
                    )
                
                downloaded_data = {
                    "message_id": sent_audio.message_id,
                    "chat_id": callback.message.chat.id,
                    "download_requester_id": current_user_id
                }
                audio_downloaded[audio_id] = downloaded_data
                await save_audio_downloaded(audio_id, downloaded_data)
                
                if audio_id in audio_url_storage:
                    storage_data = audio_url_storage[audio_id]
                    if isinstance(storage_data, dict) and "control_message_id" in storage_data:
                        try:
                            chat_id = callback.message.chat.id
                            if str(chat_id).startswith("-100"):
                                clean_chat_id = str(chat_id)[4:]
                                audio_link = f"https://t.me/c/{clean_chat_id}/{sent_audio.message_id}"
                                
                                new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="Установленное аудио", url=audio_link)],
                                    [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_slideshow:{original_user_id}:{storage_data.get('message_ids', '')}"))]
                                ])
                                
                                await bot.edit_message_reply_markup(
                                    chat_id=storage_data["control_message_chat_id"],
                                    message_id=storage_data["control_message_id"],
                                    reply_markup=new_keyboard
                                )
                        except Exception as edit_error:
                            logger.warning(f"⚠️ Не удалось обновить кнопку слайдшоу: {edit_error}")
                
                try:
                    os.remove(audio_path)
                except Exception:
                    pass
                
            except Exception as e:
                logger.error(f"❌ Ошибка при отправке аудио слайдшоу: {e}")
                await callback.answer("❌ Ошибка при отправке аудио", show_alert=True)
        else:
            await callback.answer("❌ Ошибка данных", show_alert=True)
    
    elif action in ["download_audio", "dl_audio"]:
        # Формат: dl_audio:short_url или dl_audio:short_url:original_msg_id
        try:
            if len(callback_data) < 2:
                await callback.answer("❌ Неверный формат", show_alert=True)
                return

            # URL может быть укорочен, нужно получить полный из сообщения
            # Попробуем найти URL в тексте сообщения или caption
            url = None
            if callback.message.caption:
                for word in callback.message.caption.split():
                    if word.startswith('http'):
                        url = word
                        break

            if not url:
                await callback.answer("❌ URL не найден в сообщении", show_alert=True)
                return

            logger.info(f"🎵 dl_audio: Извлечен URL из caption: {url}")

            # Проверяем кэш перед загрузкой
            clean = clean_url(url)
            cached = await get_media_cache(clean)

            if cached and cached[1]:
                # Аудио есть в кэше - отправляем из кэша
                audio_file_id = cached[1]
                logger.info(f"🎵 dl_audio: Аудио найдено в кэше, отправляем")

                # Создаем caption для аудио через кнопку
                caption = create_media_caption(callback.from_user, media_type="audio", audio_from_button=True)

                # Получаем название из caption видео
                audio_title = "Аудио"
                if callback.message.caption:
                    lines = callback.message.caption.split('\n')
                    for line in lines:
                        if line.startswith('🎬'):
                            audio_title = line.replace('🎬', '').strip()
                            break
                        elif line and not line.startswith('http') and '@' not in line:
                            audio_title = line.strip()
                            break

                # Создаем кнопку удаления
                audio_id = f"cached_audio_{callback.from_user.id}_{int(time.time())}"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{callback.from_user.id}:{audio_id}"))]
                ])

                sent_audio = await bot.send_audio(
                    callback.message.chat.id,
                    audio_file_id,
                    caption=caption,
                    title=audio_title,
                    reply_markup=keyboard
                )

                # Сохраняем информацию об аудио для возможности удаления
                audio_data = {
                    "message_id": sent_audio.message_id,
                    "chat_id": callback.message.chat.id,
                    "file_id": sent_audio.audio.file_id if sent_audio.audio else None
                }
                audio_downloaded[audio_id] = audio_data
                await save_audio_downloaded(audio_id, audio_data)

                # Обновляем кнопку на видео
                try:
                    chat_id = callback.message.chat.id
                    video_msg_id = callback.message.message_id

                    if str(chat_id).startswith("-100"):
                        # Групповой чат - показываем кнопку со ссылкой на аудио
                        clean_chat_id = str(chat_id)[4:]
                        audio_link = f"https://t.me/c/{clean_chat_id}/{sent_audio.message_id}"

                        new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🎵 Аудио", url=audio_link)],
                            [InlineKeyboardButton(text="🗑 Удалить", callback_data=secure_callback("delete_message"))]
                        ])
                    else:
                        # Личный чат - удаляем кнопку аудио
                        new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🗑 Удалить", callback_data=secure_callback("delete_message"))]
                        ])

                    await bot.edit_message_reply_markup(
                        chat_id=chat_id,
                        message_id=video_msg_id,
                        reply_markup=new_keyboard
                    )
                except Exception as e:
                    logger.error(f"❌ Ошибка обновления кнопки видео из кэша: {e}")

                await callback.answer("✅ Аудио из кэша")
                return

            # Аудио нет в кэше - загружаем
            status_msg = await callback.message.reply("⏳ Загрузка аудио...")

            downloader = find_downloader(url)
            if not downloader:
                try:
                    await status_msg.delete()
                except:
                    pass
                await callback.answer("❌ Платформа не поддерживается", show_alert=True)
                return
            
            temp_dir = tempfile.mkdtemp()
            try:
                # Сначала скачиваем видео через загрузчик платформы, потом извлекаем аудио
                await status_msg.edit_text("⏳ Скачиваю видео для извлечения аудио...")
                
                # Скачиваем видео через загрузчик (он знает как работать с платформой)
                result = await downloader.download(url, temp_dir)
                
                if not result or not result.get('file_path') or not os.path.exists(result['file_path']):
                    # Фоллбэк: пробуем напрямую через yt-dlp с аудио
                    logger.info(f"🎵 dl_audio: Загрузчик не дал результат, пробуем yt-dlp напрямую")
                    await status_msg.edit_text("⏳ Пробую альтернативный метод...")
                    
                    output_template = os.path.join(temp_dir, '%(title)s.%(ext)s')
                    process = await asyncio.create_subprocess_exec(
                        'yt-dlp',
                        '-f', 'bestaudio',
                        '-x',
                        '--audio-format', 'mp3',
                        '--no-playlist',
                        '-o', output_template,
                        url,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
                    
                    if process.returncode != 0:
                        error_text = stderr.decode('utf-8', errors='replace')[:500]
                        try:
                            await status_msg.delete()
                        except:
                            pass
                        await callback.answer("❌ Ошибка загрузки аудио", show_alert=True)
                        return

                    # Ищем скачанный аудио файл
                    audio_extensions = ('*.mp3', '*.m4a', '*.opus', '*.ogg', '*.webm', '*.wav', '*.flac', '*.aac')
                    files = []
                    for ext in audio_extensions:
                        files.extend(Path(temp_dir).glob(ext))

                    if not files:
                        try:
                            await status_msg.delete()
                        except:
                            pass
                        await callback.answer("❌ Аудио файл не найден", show_alert=True)
                        return
                    
                    audio_path = str(files[0])
                else:
                    # Видео скачано успешно, извлекаем аудио через ffmpeg
                    video_path = result['file_path']
                    audio_path = os.path.join(temp_dir, f"audio_{int(time.time())}.mp3")
                    
                    await status_msg.edit_text("⏳ Извлекаю аудио из видео...")
                    
                    process = await asyncio.create_subprocess_exec(
                        'ffmpeg', '-i', video_path,
                        '-vn', '-acodec', 'libmp3lame', '-q:a', '2',
                        '-y', audio_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    await asyncio.wait_for(process.communicate(), timeout=120)

                    if process.returncode != 0 or not os.path.exists(audio_path):
                        try:
                            await status_msg.delete()
                        except:
                            pass
                        await callback.answer("❌ Ошибка извлечения аудио", show_alert=True)
                        return

                file_size = os.path.getsize(audio_path)

                max_size = MAX_UPLOAD_SIZE_MB * 1024 * 1024
                if file_size > max_size:
                    try:
                        await status_msg.delete()
                    except:
                        pass
                    await callback.answer(f"❌ Аудио слишком большое: {file_size/(1024*1024):.1f} MB", show_alert=True)
                    return

                # Создаем url_id ДО создания клавиатуры
                url_id = hashlib.md5(url.encode()).hexdigest()[:16]

                username_display = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.first_name

                # Получаем название видео из caption если есть
                video_title = ""
                if callback.message.caption:
                    lines = callback.message.caption.split('\n')
                    for line in lines:
                        if line.startswith('🎬'):
                            video_title = line
                            break

                audio_file = FSInputFile(audio_path)

                # Создаем клавиатуру для аудио
                audio_buttons = []
                chat_id = callback.message.chat.id

                # Добавляем кнопку "Оригинальное видео" только для групп
                if str(chat_id).startswith("-100"):
                    clean_chat_id = str(chat_id)[4:]
                    video_link = f"https://t.me/c/{clean_chat_id}/{callback.message.message_id}"
                    audio_buttons.append([InlineKeyboardButton(text="📹 Оригинальное видео", url=video_link)])

                audio_buttons.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=secure_callback(f"delete_audio:{callback.from_user.id}:{url_id}"))])
                keyboard = InlineKeyboardMarkup(inline_keyboard=audio_buttons)

                caption_text = f"🎵 Аудио\n{username_display}" if not video_title else f"🎵 {video_title.replace('🎬', '')}\n{username_display}"

                # Получаем название для аудио файла
                audio_title = "Аудио"
                if video_title:
                    # Убираем эмодзи и лишние символы
                    audio_title = video_title.replace('🎬', '').strip()
                elif callback.message.caption:
                    # Пытаемся извлечь название из caption
                    lines = callback.message.caption.split('\n')
                    for line in lines:
                        if line and not line.startswith('http') and '@' not in line:
                            audio_title = line.strip()
                            break

                sent_audio = await callback.message.reply_audio(
                    audio=audio_file,
                    caption=caption_text,
                    title=audio_title,
                    reply_markup=keyboard
                )
                
                # ВАЖНО: Извлекаем callback_data старой кнопки delete_parts ПЕРЕД сохранением
                old_delete_callback = None
                old_delete_button_text = None
                if callback.message.reply_markup and callback.message.reply_markup.inline_keyboard:
                    for row in callback.message.reply_markup.inline_keyboard:
                        for button in row:
                            if button.callback_data:
                                real_bd = verify_callback(button.callback_data)
                                if real_bd and real_bd.startswith("delete_parts:"):
                                    old_delete_callback = button.callback_data
                                old_delete_button_text = button.text
                                if DEBUG_MODE:
                                    logger.info(f"🔍 Сохраняем старую кнопку для БД: text='{old_delete_button_text}', callback='{old_delete_callback}'")
                                break
                        if old_delete_callback:
                            break
                
                # Сохраняем информацию о скачанном аудио
                downloaded_data = {
                    "message_id": sent_audio.message_id,
                    "chat_id": callback.message.chat.id,
                    "file_id": sent_audio.audio.file_id if sent_audio.audio else None,
                    "video_message_id": callback.message.message_id,  # Сохраняем ID исходного видео
                    "video_url": url,  # Сохраняем URL для восстановления кнопки
                    "delete_callback": old_delete_callback,  # Сохраняем callback кнопки удаления для разделенных видео
                    "delete_button_text": old_delete_button_text  # Сохраняем текст кнопки
                }
                audio_downloaded[url_id] = downloaded_data
                await save_audio_downloaded(url_id, downloaded_data)

                if DEBUG_MODE:
                    logger.info(f"💾 Аудио сохранено в БД: url_id={url_id}, audio_msg_id={sent_audio.message_id}, video_msg_id={callback.message.message_id}, url={url}")
                
                # Обновляем кнопку в исходном сообщении (КОПИРУЕМ ЛОГИКУ ОТ TIKTOK)
                try:
                    chat_id = callback.message.chat.id
                    video_msg_id = callback.message.message_id  # ID сообщения с видео
                    
                    if DEBUG_MODE:
                        logger.info(f"🔄 Начало обновления кнопки: chat_id={chat_id}, video_msg_id={video_msg_id}, starts_with_-100={str(chat_id).startswith('-100')}")
                    
                    # ВАЖНО: Извлекаем all_msg_ids из старой кнопки delete_parts, если это разделенное видео
                    old_delete_callback = None
                    if callback.message.reply_markup and callback.message.reply_markup.inline_keyboard:
                        for row in callback.message.reply_markup.inline_keyboard:
                            for button in row:
                                if button.callback_data:
                                    real_bd2 = verify_callback(button.callback_data)
                                    if real_bd2 and real_bd2.startswith("delete_parts:"):
                                        old_delete_callback = button.callback_data
                                    if DEBUG_MODE:
                                        logger.info(f"🔍 Найдена старая кнопка delete_parts: {old_delete_callback}")
                                    break
                            if old_delete_callback:
                                break
                    
                    if str(chat_id).startswith("-100"):
                        # Групповой чат - показываем кнопку со ссылкой
                        clean_chat_id = str(chat_id)[4:]
                        audio_link = f"https://t.me/c/{clean_chat_id}/{sent_audio.message_id}"

                        if DEBUG_MODE:
                            logger.info(f"🔗 Создана ссылка на аудио: {audio_link}")

                        # Сохраняем callback_data для кнопки удаления (с all_msg_ids если есть)
                        delete_callback = old_delete_callback if old_delete_callback else "delete_message"

                        new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🎵 Аудио", url=audio_link)],
                            [InlineKeyboardButton(text="🗑 Удалить все части" if old_delete_callback else "🗑 Удалить", callback_data=delete_callback)]
                        ])
                        
                        # ИСПОЛЬЗУЕМ bot.edit_message_reply_markup КАК В TIKTOK
                        if DEBUG_MODE:
                            logger.info(f"✏️ Вызываем bot.edit_message_reply_markup(chat_id={chat_id}, message_id={video_msg_id}, delete_callback={delete_callback})")
                        await bot.edit_message_reply_markup(
                            chat_id=chat_id,
                            message_id=video_msg_id,
                            reply_markup=new_keyboard
                        )
                        if DEBUG_MODE:
                            logger.info(f"✅ Кнопка обновлена на 'Установленное аудио' для /dw видео")
                    else:
                        # Личный чат - удаляем кнопку скачивания аудио
                        if DEBUG_MODE:
                            logger.info(f"👤 Личный чат - удаляем кнопку аудио")
                        
                        # Сохраняем callback_data для кнопки удаления (с all_msg_ids если есть)
                        delete_callback = old_delete_callback if old_delete_callback else "delete_message"
                        
                        new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🗑 Удалить все части" if old_delete_callback else "🗑 Удалить", callback_data=delete_callback)]
                        ])
                        
                        await bot.edit_message_reply_markup(
                            chat_id=chat_id,
                            message_id=video_msg_id,
                            reply_markup=new_keyboard
                        )
                        if DEBUG_MODE:
                            logger.info(f"✅ Кнопка аудио удалена в личном чате")
                except Exception as e:
                    logger.error(f"❌ Ошибка обновления кнопки: {e}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                
                await status_msg.delete()
                await callback.answer("✅ Аудио загружено")
                
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
                
        except Exception as e:
            logger.error(f"Ошибка загрузки аудио: {e}")
            await callback.answer("❌ Ошибка загрузки", show_alert=True)


def register_callback_handlers(dp):
    """Регистрация обработчиков callback-кнопок"""
    dp.include_router(router)
