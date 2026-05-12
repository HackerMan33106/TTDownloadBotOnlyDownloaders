"""
Обработчики сообщений с TikTok ссылками
"""
import os
import time
import asyncio

from aiogram import Router, types, F, Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, FSInputFile

from config.settings import (
    DEBUG_MODE,
    PERMANENT_ADMIN,
    logger,
    MAX_UPLOAD_SIZE_MB
)
from database.admins import is_admin, get_all_admins
from database.users import ruser
from database.limits import get_user_limit, check_and_increment_usage, decrement_usage
from database.audio import audio_url_storage, save_audio_url_storage
from database.whitelist import is_user_whitelisted, is_group_whitelisted
from services.downloader import download_tiktok_content
from utils.helpers import get_user_link
from utils.crypto import secure_callback
from utils.tiktok import (
    is_tiktok_url,
    extract_all_tiktok_urls,
    clean_tiktok_url,
    is_tiktok_slideshow
)
from utils.social import (
    extract_social_url,
    get_platform_name,
    get_platform_display_name,
    get_platform_emoji,
    clean_social_url
)
from services.social_downloader import download_social_content


router = Router()

def has_tiktok_url(message: types.Message) -> bool:
    """Проверяет, содержит ли сообщение TikTok ссылку"""
    return message.text and is_tiktok_url(message.text)


def has_social_url(message: types.Message) -> bool:
    """Проверяет, содержит ли сообщение ссылку на Facebook"""
    if not message.text or is_tiktok_url(message.text):
        return False
    url = extract_social_url(message.text)
    if not url:
        return False
    platform = get_platform_name(url)
    # Автоскачивание только для Facebook; Reddit и Twitter — через /an
    return platform == "facebook"


@router.message(F.text & ~F.text.startswith('/'), has_tiktok_url)
async def handle_message(message: types.Message, bot: Bot):
    """Обработчик сообщений с TikTok ссылками"""
    # Запоминаем пользователя в базу для возможности использования @username
    await ruser(message.from_user)

    user_id = message.from_user.id

    # Извлекаем все TikTok ссылки из сообщения для логирования
    urls = extract_all_tiktok_urls(message.text)
    user_link = get_user_link(message.from_user)

    # Проверяем whitelist групп (если это групповой чат)
    if message.chat.type in ['group', 'supergroup']:
        if not await is_group_whitelisted(message.chat.id):
            await message.reply("❌ Бот не может работать в этой группе")
            logger.info(f"🚫 Доступ запрещен для группы {message.chat.id} ({message.chat.title}) | {user_link} отправил: {urls}")
            return
        # Логируем Chat ID для удобства добавления в whitelist
        logger.info(f"📱 Группа: {message.chat.title} | Chat ID: {message.chat.id}")
    else:
        # Проверяем whitelist пользователей только для личных сообщений
        if not await is_user_whitelisted(message.from_user.id):
            await message.reply("❌ У вас нет доступа к этому боту")
            logger.info(f"🚫 Доступ запрещен для пользователя {user_link} | Отправил: {urls}")
            return

    # Проверяем что ссылки найдены
    if not urls:
        await message.reply("❌ Не удалось распознать ссылки")
        return

    logger.info(f"📥 {user_link}: найдено {len(urls)} ссылок")

    only_audio = message.text.strip().endswith('-a') or message.text.strip().endswith('-ф')

    # Обрабатываем ссылки параллельно
    tasks = []
    for i, url in enumerate(urls):
        if len(urls) > 1:
            msg = await message.reply(f"⏳ Обрабатываю {i+1}/{len(urls)}...")
        else:
            msg = await message.reply("⏳ Обрабатываю...")

        tasks.append(process_single_url(message, url, msg, user_link, bot, only_audio=only_audio))

    # Запускаем все задачи параллельно
    await asyncio.gather(*tasks)

    # Удаляем исходное сообщение пользователя после обработки всех ссылок
    try:
        await message.delete()
    except Exception:
        pass


async def process_single_url(original_message: types.Message, url: str, msg: types.Message, user_link: str, bot: Bot, only_audio: bool = False):
    """Обрабатывает одну TikTok ссылку"""
    # Очищаем URL для отображения в caption
    clean_url = clean_tiktok_url(url)
    
    if not clean_url:
        _err_ctx = "неподдерживаемый или нераспознанный формат ссылки TikTok"
        # Error logged
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_error:{original_message.from_user.id}"))]
        ])
        await msg.edit_text("❌ Произошла ошибка при обработке запроса", reply_markup=keyboard)
        return
    
    # Получаем username текущего пользователя для caption
    username_display = f"@{original_message.from_user.username}" if original_message.from_user.username else original_message.from_user.first_name
    user_id = original_message.from_user.id
    
    # Проверяем лимит пользователя и формируем caption
    limit_data = await get_user_limit(user_id)
    if limit_data and user_id not in PERMANENT_ADMIN and user_id not in await get_all_admins():
        max_uses, current_uses, _ = limit_data
        remaining = max_uses - current_uses
        
        # Склонение для "использование"
        if remaining % 10 == 1 and remaining % 100 != 11:
            use_word = "использование"
        elif remaining % 10 in [2, 3, 4] and remaining % 100 not in [12, 13, 14]:
            use_word = "использования"
        else:
            use_word = "использований"
        
        caption_text = f"{username_display}, у вас осталось {remaining}/{max_uses} {use_word} бота на этот день\n{clean_url}"
    else:
        # Для пользователей без ограничений
        caption_text = f"{username_display}\n{clean_url}"
    
    try:
        from database.db import get_media_cache, set_media_cache
        from config.settings import TRASH_GROUP_ID
        import hashlib

        cached = await get_media_cache(clean_url)
        is_slideshow = is_tiktok_slideshow(clean_url)

        # Проверяем лимит ТОЛЬКО если контент не в кэше или это не аудио
        # Аудио не учитывается в лимитах
        needs_limit_check = False
        if only_audio:
            # Аудио всегда без лимита
            needs_limit_check = False
        else:
            # Видео: проверяем лимит только если нет в кэше
            if not (cached and cached[0] and not is_slideshow):
                needs_limit_check = True

        if needs_limit_check:
            if not await check_and_increment_usage(user_id):
                limit_data = await get_user_limit(user_id)
                if limit_data:
                    max_uses = limit_data[0]

                    if max_uses == 0:
                        from utils.helpers import create_delete_button
                        await msg.edit_text(
                            "🚫 Использование бота для вас заблокировано.",
                            reply_markup=create_delete_button(original_message)
                        )
                        try:
                            await original_message.delete()
                        except:
                            pass
                        logger.info(f"🚫 Заблокированный пользователь {user_id} попытался использовать бота")
                        return

                    from datetime import datetime, timezone, timedelta
                    utc_plus_1 = timezone(timedelta(hours=1))
                    current_time = datetime.now(utc_plus_1)
                    midnight = (current_time + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                    time_diff = midnight - current_time

                    hours = time_diff.seconds // 3600
                    minutes = (time_diff.seconds % 3600) // 60

                    reset_time_str = midnight.strftime("%H:%M")
                    countdown = f"{hours}ч {minutes}м"

                    from utils.helpers import create_delete_button
                    await msg.edit_text(
                        f"❌ Вы превысили дневной лимит использований ({max_uses} раз)\n"
                        f"⏰ Сброс в {reset_time_str} UTC+1 (через {countdown})",
                        reply_markup=create_delete_button(original_message)
                    )

                    try:
                        await original_message.delete()
                    except:
                        pass

                    logger.info(f"🚫 Лимит превышен для пользователя {user_id}")
                return

        # Логируем оставшиеся использования для пользователей с лимитом
        limit_data = await get_user_limit(user_id)
        if limit_data and needs_limit_check:
            max_uses, current_uses, _ = limit_data
            logger.info(f"📊 Пользователь {user_id}: использовано {current_uses}/{max_uses}")

        if only_audio:
            if cached and cached[1]:
                # Создаем кнопку удаления для аудио
                audio_id = f"audio_only_{user_id}_{int(time.time())}"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{user_id}:{audio_id}"))]
                ])

                sent_audio = await bot.send_audio(
                    original_message.chat.id,
                    cached[1],
                    caption=caption_text,
                    title="Аудио",
                    reply_markup=keyboard
                )

                # Сохраняем информацию об аудио для возможности удаления
                from database.audio import audio_downloaded, save_audio_downloaded
                audio_data = {
                    "message_id": sent_audio.message_id,
                    "chat_id": original_message.chat.id,
                    "file_id": cached[1]
                }
                audio_downloaded[audio_id] = audio_data
                await save_audio_downloaded(audio_id, audio_data)

                await msg.delete()
                return
            elif is_slideshow:
                # Для слайдшоу с флагом -a отправляем только аудио
                from database.db import get_slideshow_cache
                cached_slideshow = await get_slideshow_cache(clean_url)
                if cached_slideshow and cached_slideshow[1]:
                    # Есть закэшированное аудио
                    audio_id = f"audio_only_{user_id}_{int(time.time())}"
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{user_id}:{audio_id}"))]
                    ])

                    sent_audio = await bot.send_audio(
                        original_message.chat.id,
                        cached_slideshow[1],
                        caption=caption_text,
                    title="Аудио",
                        reply_markup=keyboard
                    )

                    # Сохраняем информацию об аудио для возможности удаления
                    from database.audio import audio_downloaded, save_audio_downloaded
                    audio_data = {
                        "message_id": sent_audio.message_id,
                        "chat_id": original_message.chat.id,
                        "file_id": cached_slideshow[1]
                    }
                    audio_downloaded[audio_id] = audio_data
                    await save_audio_downloaded(audio_id, audio_data)

                    await msg.delete()
                    return
                else:
                    # Аудио не закэшировано, нужно скачать слайдшоу
                    pass
        else:
            # Проверяем кэш для слайдшоу
            if is_slideshow:
                from database.db import get_slideshow_cache
                cached_slideshow = await get_slideshow_cache(clean_url)

                if cached_slideshow and cached_slideshow[0]:
                    # Слайдшоу найдено в кэше, отправляем из кэша
                    cached_photo_ids, cached_audio_id = cached_slideshow
                    logger.info(f"✅ Слайдшоу найдено в кэше ({len(cached_photo_ids)} фото)")

                    await msg.edit_text("📤 Отправляю из кэша...")

                    # Отправляем фото из кэша
                    from aiogram.types import InputMediaPhoto
                    MAX_PHOTOS_PER_GROUP = 10
                    all_sent_messages = []

                    for chunk_start in range(0, len(cached_photo_ids), MAX_PHOTOS_PER_GROUP):
                        chunk_end = min(chunk_start + MAX_PHOTOS_PER_GROUP, len(cached_photo_ids))
                        chunk_file_ids = cached_photo_ids[chunk_start:chunk_end]

                        media_group = []
                        for i, file_id in enumerate(chunk_file_ids):
                            is_last_photo_overall = (chunk_end == len(cached_photo_ids)) and (i == len(chunk_file_ids) - 1)

                            if is_last_photo_overall:
                                media_group.append(InputMediaPhoto(
                                    media=file_id,
                                    caption=caption_text
                                ))
                            else:
                                media_group.append(InputMediaPhoto(media=file_id))

                        messages = await bot.send_media_group(
                            chat_id=original_message.chat.id,
                            media=media_group
                        )
                        all_sent_messages.extend(messages)

                        if chunk_end < len(cached_photo_ids):
                            await asyncio.sleep(1)

                    # Создаем кнопки
                    message_ids = ",".join([str(m.message_id) for m in all_sent_messages])
                    slideshow_id = f"slideshow_{user_id}_{int(time.time())}"

                    buttons = []
                    if cached_audio_id:
                        audio_id = f"slideshow_audio_{user_id}_{int(time.time())}"
                        audio_data = {
                            "audio_file_id": cached_audio_id,
                            "slideshow_url": clean_url,
                            "control_message_chat_id": original_message.chat.id,
                            "message_ids": message_ids,
                            "last_media_group_message_id": all_sent_messages[-1].message_id,
                            "slideshow_id": slideshow_id,
                            "type": "slideshow_audio"
                        }
                        audio_url_storage[audio_id] = audio_data
                        await save_audio_url_storage(audio_id, audio_data)
                        buttons.append([InlineKeyboardButton(text="🎵 Установленное аудио", callback_data=secure_callback(f"send_slideshow_audio:{audio_id}"))])
                    else:
                        slideshow_data = {
                            "message_ids": message_ids,
                            "type": "slideshow"
                        }
                        audio_url_storage[slideshow_id] = slideshow_data
                        await save_audio_url_storage(slideshow_id, slideshow_data)

                    buttons.append([InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_slideshow:{user_id}:{slideshow_id}"))])
                    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

                    control_message = await bot.send_message(
                        chat_id=original_message.chat.id,
                        text="Удалить изображения или отправить аудио?",
                        reply_markup=keyboard
                    )

                    if cached_audio_id:
                        audio_url_storage[audio_id]["control_message_id"] = control_message.message_id
                        await save_audio_url_storage(audio_id, audio_url_storage[audio_id])

                    await msg.delete()
                    logger.info(f"✅ {user_link} - слайдшоу из кэша ({len(cached_photo_ids)} фото)")
                    return

            # Проверяем кэш для видео
            if cached and cached[0] and not is_slideshow:
                url_id = f"{user_id}_{int(time.time())}"
                url_hash = hashlib.md5(clean_url.encode()).hexdigest()[:16]
                if cached[1]:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🎵 Установленное аудио", callback_data=f"send_cached_audio:{url_hash}")],
                        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_video:{user_id}"))]
                    ])
                else:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📥 Установленное аудио", callback_data=f"extract_audio:{url_id}")],
                        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_video:{user_id}"))]
                    ])

                video_data = {
                    "url": clean_url,
                    "chat_id": original_message.chat.id,
                    "owner_id": user_id  # Сохраняем ID владельца видео
                }
                audio_url_storage[url_id] = video_data
                await save_audio_url_storage(url_id, video_data)

                sent_video = await bot.send_video(original_message.chat.id, cached[0], caption=caption_text, reply_markup=keyboard)

                video_data["video_message_id"] = sent_video.message_id
                await save_audio_url_storage(url_id, video_data)

                await msg.delete()
                return
        
        # Показываем статус для слайдшоу (они скачиваются дольше)
        if is_slideshow:
            await msg.edit_text("⏳ Скачиваю слайдшоу...")
        
        # Передаем уже очищенный URL
        content_path, content_type = await download_tiktok_content(clean_url)
        
        # Проверяем на возрастное ограничение
        if content_path:
            if content_type == "slideshow":
                await _process_slideshow(original_message, content_path, caption_text, clean_url, user_id, user_link, msg, bot, only_audio)
            else:
                await _process_video(original_message, content_path, caption_text, clean_url, user_id, user_link, msg, bot, only_audio)
        else:
            content_name = "слайдшоу" if is_tiktok_slideshow(url) else "видео"
            _err_ctx = f"не удалось скачать {content_name}"
            # Error logged
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_error:{user_id}"))]
            ])
            await msg.edit_text(
                "❌ Произошла ошибка при обработке запроса",
                reply_markup=keyboard
            )
            logger.error(f"❌ Не удалось скачать {content_name} из ссылки: {url}")
            
            # Откатываем счётчик использований при ошибке
            limit_data = await get_user_limit(user_id)
            if limit_data:
                await decrement_usage(user_id)
                logger.info(f"⏪ Откат счётчика для пользователя {user_id} из-за ошибки")
            
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке {url}: {str(e)}")
        try:
            user_id = original_message.from_user.id
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_error:{user_id}"))]
            ])
            _err_ctx = f"необработанная ошибка: {str(e)[:80]}"
            # Error logged
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_error:{user_id}"))]
            ])
            await msg.edit_text(
                "❌ Произошла ошибка при обработке запроса",
                reply_markup=keyboard
            )
            
            # Откатываем счётчик использований при ошибке
            limit_data = await get_user_limit(user_id)
            if limit_data:
                await decrement_usage(user_id)
                logger.info(f"⏪ Откат счётчика для пользователя {user_id} из-за ошибки обработки")
        except Exception:
            await original_message.reply("❌ Произошла ошибка при обработке запроса")


async def _process_slideshow(original_message, content_path, caption_text, clean_url, user_id, user_link, msg, bot, only_audio=False):
    """Обработка слайдшоу"""
    if len(content_path) > 100:
        await msg.edit_text("❌ Слишком много фотографий (макс. 100)")
        return

    total_size = 0
    valid_photos = []
    audio_file_path = None

    # Проверяем файлы
    for photo_path in content_path:
        if os.path.exists(photo_path):
            size_mb = os.path.getsize(photo_path) / (1024 * 1024)

            # Проверяем аудио файлы (для слайдшоу TikTok)
            if photo_path.lower().endswith(('.mp3', '.m4a', '.wav')):
                if not audio_file_path and size_mb < 30:
                    audio_file_path = photo_path
                    logger.info(f"🎵 Найден аудио файл для слайдшоу: {photo_path} ({size_mb:.1f}MB)")
                continue

            # Проверяем изображения (пропускаем если only_audio)
            if not only_audio:
                if (size_mb < 300 and
                    photo_path.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'))):
                    valid_photos.append(photo_path)
                    total_size += size_mb
                else:
                    file_ext = os.path.splitext(photo_path)[1].lower()
                    logger.warning(f"⚠️ Пропускаем файл {photo_path}: размер {size_mb:.1f}MB, формат {file_ext}")

    # Если only_audio, проверяем наличие аудио
    if only_audio:
        if not audio_file_path:
            await msg.edit_text("❌ Аудио не найдено в слайдшоу")
            return

        # Отправляем только аудио
        from config.settings import TRASH_GROUP_ID
        from database.db import get_slideshow_cache, set_slideshow_cache

        # Проверяем кэш для получения audio_file_id
        cached_slideshow = await get_slideshow_cache(clean_url)
        audio_file_id_cached = cached_slideshow[1] if cached_slideshow else None

        # Кэшируем аудио если еще не закэшировано
        if TRASH_GROUP_ID and not audio_file_id_cached:
            try:
                from utils.crypto import secure_callback
                import hashlib

                url_hash = hashlib.md5(clean_url.encode()).hexdigest()
                cache_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑️ Удалить из кэша", callback_data=secure_callback(f"clear_cache:{url_hash}"))]
                ])

                trash_audio = await bot.send_audio(
                    chat_id=TRASH_GROUP_ID,
                    audio=FSInputFile(audio_file_path),
                    title="Аудио",
                    caption=clean_url,
                    reply_markup=cache_keyboard
                )
                audio_file_id_cached = trash_audio.audio.file_id

                # Сохраняем в кэш (без фото, только аудио)
                await set_slideshow_cache(clean_url, [], audio_file_id_cached)
                logger.info(f"✅ Аудио слайдшоу закэшировано")
            except Exception as cache_err:
                logger.warning(f"⚠️ Не удалось закэшировать аудио: {cache_err}")

        # Отправляем аудио пользователю
        audio_id = f"audio_only_{user_id}_{int(time.time())}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{user_id}:{audio_id}"))]
        ])

        if audio_file_id_cached:
            sent_audio = await bot.send_audio(
                original_message.chat.id,
                audio_file_id_cached,
                caption=caption_text,
                title="",
                reply_markup=keyboard
            )
        else:
            sent_audio = await bot.send_audio(
                original_message.chat.id,
                FSInputFile(audio_file_path),
                caption=caption_text,
                title="",
                reply_markup=keyboard
            )

        # Сохраняем информацию об аудио
        from database.audio import audio_downloaded, save_audio_downloaded
        audio_data = {
            "message_id": sent_audio.message_id,
            "chat_id": original_message.chat.id,
            "file_id": sent_audio.audio.file_id
        }
        audio_downloaded[audio_id] = audio_data
        await save_audio_downloaded(audio_id, audio_data)

        await msg.delete()
        logger.info(f"✅ {user_link} - аудио из слайдшоу отправлено")
        return

    if not valid_photos:
        await msg.edit_text("❌ Нет подходящих фотографий")
        return

    if total_size > MAX_UPLOAD_SIZE_MB and not await is_admin(user_id):
        await msg.edit_text(f"❌ Слайдшоу больше {MAX_UPLOAD_SIZE_MB}MB")
        return
    
    try:
        from config.settings import TRASH_GROUP_ID
        from database.db import get_slideshow_cache, set_slideshow_cache
        from utils.crypto import secure_callback
        import hashlib

        # Проверяем кэш слайдшоу
        cached_slideshow = await get_slideshow_cache(clean_url)
        cached_photo_ids = []
        cached_audio_id = None

        if cached_slideshow:
            cached_photo_ids, cached_audio_id = cached_slideshow
            logger.info(f"✅ Слайдшоу найдено в кэше ({len(cached_photo_ids)} фото)")

        # Сначала отправляем в мусорную группу для кэширования (если настроено и не закэшировано)
        trash_messages = []
        if TRASH_GROUP_ID and not cached_photo_ids:
            try:
                logger.info(f"📦 Кэширование слайдшоу в мусорную группу...")
                MAX_PHOTOS_PER_GROUP = 10

                for chunk_start in range(0, len(valid_photos), MAX_PHOTOS_PER_GROUP):
                    chunk_end = min(chunk_start + MAX_PHOTOS_PER_GROUP, len(valid_photos))
                    chunk_photos = valid_photos[chunk_start:chunk_end]

                    media_group = []
                    for i, photo_path in enumerate(chunk_photos):
                        is_last_photo_overall = (chunk_end == len(valid_photos)) and (i == len(chunk_photos) - 1)

                        # Не добавляем caption к последнему фото - добавим его позже вместе с кнопкой
                        if is_last_photo_overall:
                            media_group.append(InputMediaPhoto(media=FSInputFile(photo_path)))
                        else:
                            media_group.append(InputMediaPhoto(media=FSInputFile(photo_path)))

                    # Отправляем в мусорную группу с обработкой flood control
                    retry_count = 0
                    max_retries = 3
                    while retry_count < max_retries:
                        try:
                            messages = await bot.send_media_group(
                                chat_id=TRASH_GROUP_ID,
                                media=media_group
                            )
                            trash_messages.extend(messages)
                            break
                        except Exception as send_err:
                            from aiogram.exceptions import TelegramRetryAfter
                            if isinstance(send_err, TelegramRetryAfter):
                                retry_after = send_err.retry_after
                                logger.warning(f"⚠️ Flood control при кэшировании: ожидание {retry_after}s")
                                await asyncio.sleep(retry_after + 1)
                                retry_count += 1
                            else:
                                raise

                    if chunk_end < len(valid_photos):
                        await asyncio.sleep(1)

                # Добавляем кнопку удаления и caption к последнему сообщению
                if trash_messages:
                    try:
                        url_hash = hashlib.md5(clean_url.encode()).hexdigest()[:16]
                        cache_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🗑️ Удалить из кэша", callback_data=secure_callback(f"clear_cache:{url_hash}"))]
                        ])

                        last_msg = trash_messages[-1]
                        await bot.edit_message_caption(
                            chat_id=TRASH_GROUP_ID,
                            message_id=last_msg.message_id,
                            caption=clean_url,
                            reply_markup=cache_keyboard
                        )
                    except Exception as e:
                        logger.warning(f"⚠️ Не удалось добавить кнопку к слайдшоу: {e}")

                # Сохраняем file_ids в кэш
                cached_photo_ids = [msg.photo[-1].file_id for msg in trash_messages]
                logger.info(f"✅ Слайдшоу закэшировано в мусорной группе ({len(trash_messages)} сообщений)")
            except Exception as cache_err:
                logger.warning(f"⚠️ Не удалось закэшировать слайдшоу: {cache_err}")

        # Разбиваем фотографии на группы по 10 (лимит Telegram)
        MAX_PHOTOS_PER_GROUP = 10
        all_sent_messages = []

        # Используем cached_photo_ids если есть, иначе отправляем файлы
        if cached_photo_ids:
            logger.info(f"📤 Отправка слайдшоу из кэша ({len(cached_photo_ids)} фото)")
            photo_index = 0
            for chunk_start in range(0, len(cached_photo_ids), MAX_PHOTOS_PER_GROUP):
                chunk_end = min(chunk_start + MAX_PHOTOS_PER_GROUP, len(cached_photo_ids))
                chunk_file_ids = cached_photo_ids[chunk_start:chunk_end]

                media_group = []
                for i, file_id in enumerate(chunk_file_ids):
                    is_last_photo_overall = (chunk_end == len(cached_photo_ids)) and (i == len(chunk_file_ids) - 1)

                    if is_last_photo_overall:
                        media_group.append(InputMediaPhoto(
                            media=file_id,
                            caption=caption_text
                        ))
                    else:
                        media_group.append(InputMediaPhoto(media=file_id))

                # Отправляем группу фотографий с обработкой flood control
                retry_count = 0
                max_retries = 3
                while retry_count < max_retries:
                    try:
                        messages = await bot.send_media_group(
                            chat_id=original_message.chat.id,
                            media=media_group
                        )
                        all_sent_messages.extend(messages)
                        break
                    except Exception as send_err:
                        from aiogram.exceptions import TelegramRetryAfter
                        if isinstance(send_err, TelegramRetryAfter):
                            retry_after = send_err.retry_after
                            logger.warning(f"⚠️ Flood control: ожидание {retry_after}s перед повтором")
                            await asyncio.sleep(retry_after + 1)
                            retry_count += 1
                        else:
                            raise

                # Небольшая пауза между группами чтобы избежать флуд-контроля
                if chunk_end < len(cached_photo_ids):
                    await asyncio.sleep(1)
        else:
            logger.info(f"📤 Отправка слайдшоу из файлов ({len(valid_photos)} фото)")
            for chunk_start in range(0, len(valid_photos), MAX_PHOTOS_PER_GROUP):
                chunk_end = min(chunk_start + MAX_PHOTOS_PER_GROUP, len(valid_photos))
                chunk_photos = valid_photos[chunk_start:chunk_end]

                media_group = []
                for i, photo_path in enumerate(chunk_photos):
                    is_last_photo_overall = (chunk_end == len(valid_photos)) and (i == len(chunk_photos) - 1)

                    if is_last_photo_overall:
                        media_group.append(InputMediaPhoto(
                            media=FSInputFile(photo_path),
                            caption=caption_text
                        ))
                    else:
                        media_group.append(InputMediaPhoto(media=FSInputFile(photo_path)))

                # Отправляем группу фотографий с обработкой flood control
                retry_count = 0
                max_retries = 3
                while retry_count < max_retries:
                    try:
                        messages = await bot.send_media_group(
                            chat_id=original_message.chat.id,
                            media=media_group
                        )
                        all_sent_messages.extend(messages)
                        break
                    except Exception as send_err:
                        from aiogram.exceptions import TelegramRetryAfter
                        if isinstance(send_err, TelegramRetryAfter):
                            retry_after = send_err.retry_after
                            logger.warning(f"⚠️ Flood control: ожидание {retry_after}s перед повтором")
                            await asyncio.sleep(retry_after + 1)
                            retry_count += 1
                        else:
                            raise

                # Небольшая пауза между группами чтобы избежать флуд-контроля
                if chunk_end < len(valid_photos):
                    await asyncio.sleep(1)

        # Создаем список ID ВСЕХ отправленных сообщений
        message_ids = ",".join([str(message.message_id) for message in all_sent_messages])

        # Создаем уникальный ID для слайдшоу
        slideshow_id = f"slideshow_{user_id}_{int(time.time())}"

        # СРАЗУ отправляем контрольное сообщение с кнопками (до кэширования аудио)
        buttons = []
        audio_file_id_cached = cached_audio_id  # Используем из кэша если есть

        if audio_file_path:
            audio_id = f"slideshow_audio_{user_id}_{int(time.time())}"

            # Добавляем кнопку "Установленное аудио" если аудио есть
            if audio_file_id_cached:
                buttons.append([InlineKeyboardButton(text="🎵 Установленное аудио", callback_data=f"send_slideshow_audio:{audio_id}")])
            else:
                buttons.append([InlineKeyboardButton(text="📥 Скачать аудио", callback_data=f"send_slideshow_audio:{audio_id}")])

        # Добавляем кнопку удаления
        buttons.append([InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_slideshow:{user_id}:{slideshow_id}"))])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        # Отправляем контрольное сообщение
        control_message = await bot.send_message(
            chat_id=original_message.chat.id,
            text="Удалить изображения или отправить аудио?",
            reply_markup=keyboard
        )

        # Теперь кэшируем аудио в фоне (если нужно)
        if audio_file_path and TRASH_GROUP_ID and not audio_file_id_cached:
            async def cache_audio_background():
                try:
                    logger.info(f"📦 Фоновое кэширование аудио слайдшоу...")

                    url_hash = hashlib.md5(clean_url.encode()).hexdigest()
                    cache_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🗑️ Удалить из кэша", callback_data=secure_callback(f"clear_cache:{url_hash}"))]
                    ])

                    trash_audio = await bot.send_audio(
                        chat_id=TRASH_GROUP_ID,
                        audio=FSInputFile(audio_file_path),
                        title="Аудио",
                        caption=clean_url,
                        reply_markup=cache_keyboard
                    )
                    audio_file_id_cached_bg = trash_audio.audio.file_id

                    # Обновляем storage с file_id
                    if audio_id in audio_url_storage:
                        audio_url_storage[audio_id]["audio_file_id"] = audio_file_id_cached_bg
                        await save_audio_url_storage(audio_id, audio_url_storage[audio_id])

                    # Обновляем кэш слайдшоу
                    await set_slideshow_cache(clean_url, cached_photo_ids, audio_file_id_cached_bg)

                    # Обновляем кнопку на "Установленное аудио"
                    try:
                        new_buttons = [
                            [InlineKeyboardButton(text="🎵 Установленное аудио", callback_data=f"send_slideshow_audio:{audio_id}")],
                            [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_slideshow:{user_id}:{slideshow_id}"))]
                        ]
                        await bot.edit_message_reply_markup(
                            chat_id=original_message.chat.id,
                            message_id=control_message.message_id,
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=new_buttons)
                        )
                    except:
                        pass

                    logger.info(f"✅ Аудио слайдшоу закэшировано в фоне")
                except Exception as e:
                    logger.error(f"❌ Ошибка фонового кэширования аудио: {e}")

            asyncio.create_task(cache_audio_background())

        # Сохраняем данные об аудио
        if audio_file_path:
            audio_data = {
                "audio_path": audio_file_path,
                "audio_file_id": audio_file_id_cached,
                "slideshow_url": clean_url,
                "control_message_chat_id": original_message.chat.id,
                "message_ids": message_ids,
                "last_media_group_message_id": all_sent_messages[-1].message_id,
                "slideshow_id": slideshow_id,
                "type": "slideshow_audio"
            }
            audio_url_storage[audio_id] = audio_data
            await save_audio_url_storage(audio_id, audio_data)
        else:
            slideshow_data = {
                "message_ids": message_ids,
                "type": "slideshow"
            }
            audio_url_storage[slideshow_id] = slideshow_data
            await save_audio_url_storage(slideshow_id, slideshow_data)

        # Сохраняем message_id контрольного сообщения для обновления кнопки
        if audio_file_path:
            audio_url_storage[audio_id]["control_message_id"] = control_message.message_id
            await save_audio_url_storage(audio_id, audio_url_storage[audio_id])
        
        logger.info("✅ Отдельное сообщение с кнопкой создано")
        
        # Вычисляем количество групп
        num_groups = (len(valid_photos) + MAX_PHOTOS_PER_GROUP - 1) // MAX_PHOTOS_PER_GROUP
        groups_info = f" ({num_groups} групп)" if num_groups > 1 else ""

        logger.info(f"✅ {user_link} - слайдшоу {len(valid_photos)} фото{groups_info} ({total_size:.1f}MB)" + (f" + аудио" if audio_file_path else ""))

        # Удаляем статусное сообщение с обработкой flood control
        try:
            await msg.delete()
        except Exception as del_err:
            from aiogram.exceptions import TelegramRetryAfter
            if isinstance(del_err, TelegramRetryAfter):
                logger.warning(f"⚠️ Flood control при удалении сообщения, пропускаем")
            else:
                logger.warning(f"⚠️ Не удалось удалить статусное сообщение: {del_err}")

    except Exception as media_group_error:
        from aiogram.exceptions import TelegramRetryAfter
        if isinstance(media_group_error, TelegramRetryAfter):
            logger.error(f"❌ Flood control превышен при отправке слайдшоу: {media_group_error}")
            try:
                await msg.edit_text(
                    "❌ Telegram ограничил отправку сообщений. Попробуйте через несколько секунд."
                )
            except:
                pass
        else:
            logger.error(f"❌ Ошибка отправки медиа-группы: {str(media_group_error)}")
            try:
                await msg.edit_text("❌ Не удалось отправить фотографии")
            except:
                pass
    
    # Удаляем временные файлы (кроме аудио, если оно есть в storage)
    for photo_path in content_path:
        if audio_file_path and photo_path == audio_file_path:
            continue
        try:
            os.remove(photo_path)
        except:
            pass
    
    # Удаляем папку
    try:
        parent_dir = os.path.dirname(content_path[0])
        os.rmdir(parent_dir)
    except:
        pass


async def _process_video(original_message, content_path, caption_text, clean_url, user_id, user_link, msg, bot, only_audio=False):
    """Обработка видео с поддержкой кэширования в TRASH_GROUP_ID"""
    size_mb = os.path.getsize(content_path) / (1024 * 1024)

    if size_mb > MAX_UPLOAD_SIZE_MB and not await is_admin(user_id):
        await msg.edit_text(f"❌ Видео больше {MAX_UPLOAD_SIZE_MB}MB")
        return

    from config.settings import TRASH_GROUP_ID
    from database.db import set_media_cache
    import subprocess
    import time
    import hashlib

    url_id = f"{user_id}_{int(time.time())}"

    video_file_id, audio_file_id = None, None
    is_trashed = False

    # 1. Отправляем в TRASH_GROUP_ID для кэширования
    if TRASH_GROUP_ID:
        try:
            import hashlib
            from utils.crypto import secure_callback

            # Создаём кнопку для удаления из кэша
            url_hash = hashlib.md5(clean_url.encode()).hexdigest()[:16]
            cache_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить из кэша", callback_data=secure_callback(f"clear_cache:{url_hash}"))]
            ])

            # Отправляем видео с caption и кнопкой
            v_msg = await bot.send_video(
                TRASH_GROUP_ID,
                FSInputFile(content_path),
                caption=clean_url,
                reply_markup=cache_keyboard
            )
            video_file_id = v_msg.video.file_id

            # Сохраняем кэш с video_id сразу (audio будет добавлен фоново)
            await set_media_cache(clean_url, video_file_id, None)
            is_trashed = True
        except Exception as e:
            logger.error(f"Ошибка сохранения в TRASH_GROUP_ID: {e}")

    # 2. Если пользователь просил только аудио (-a или -ф)
    if only_audio:
        # Проверяем есть ли аудио в кэше
        from database.db import get_media_cache
        cached = await get_media_cache(clean_url)

        if cached and cached[1]:
            # Аудио есть в кэше - отправляем сразу
            audio_file_id = cached[1]

            # Создаем кнопку удаления для аудио
            audio_id = f"audio_only_{user_id}_{int(time.time())}"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{user_id}:{audio_id}"))]
            ])

            sent_audio = await bot.send_audio(
                original_message.chat.id,
                audio_file_id,
                caption=caption_text,
                title="",
                reply_markup=keyboard
            )

            # Сохраняем информацию об аудио для возможности удаления
            from database.audio import audio_downloaded, save_audio_downloaded
            audio_data = {
                "message_id": sent_audio.message_id,
                "chat_id": original_message.chat.id,
                "file_id": audio_file_id
            }
            audio_downloaded[audio_id] = audio_data
            await save_audio_downloaded(audio_id, audio_data)

            await msg.delete()
            return
        else:
            # Аудио нет в кэше - извлекаем из видео
            logger.info(f"🎵 Аудио не в кэше, извлекаем из видео")

            # Извлекаем аудио синхронно для немедленной отправки
            audio_path = content_path.rsplit('.', 1)[0] + '.mp3'
            try:
                from handlers.commands.download_video import extract_audio_simple
                success = await extract_audio_simple(content_path, audio_path, msg, "")

                if success and os.path.exists(audio_path):
                    # Кэшируем аудио в мусорной группе
                    if TRASH_GROUP_ID:
                        try:
                            from utils.crypto import secure_callback
                            url_hash = hashlib.md5(clean_url.encode()).hexdigest()[:16]
                            cache_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="🗑️ Удалить из кэша", callback_data=secure_callback(f"clear_cache:{url_hash}"))]
                            ])

                            a_msg = await bot.send_audio(
                                TRASH_GROUP_ID,
                                FSInputFile(audio_path),
                                title="Аудио",
                                caption=clean_url,
                                reply_markup=cache_keyboard
                            )
                            audio_file_id = a_msg.audio.file_id

                            # Обновляем кэш
                            from database.db import set_media_cache
                            await set_media_cache(clean_url, video_file_id, audio_file_id)
                            logger.info(f"✅ Аудио закэшировано")
                        except Exception as e:
                            logger.error(f"❌ Ошибка кэширования аудио: {e}")
                            audio_file_id = None

                    # Отправляем аудио пользователю
                    audio_id = f"audio_only_{user_id}_{int(time.time())}"
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{user_id}:{audio_id}"))]
                    ])

                    if audio_file_id:
                        sent_audio = await bot.send_audio(
                            original_message.chat.id,
                            audio_file_id,
                            caption=caption_text,
                            title="",
                            reply_markup=keyboard
                        )
                    else:
                        sent_audio = await bot.send_audio(
                            original_message.chat.id,
                            FSInputFile(audio_path),
                            caption=caption_text,
                            title="",
                            reply_markup=keyboard
                        )

                    # Сохраняем информацию об аудио
                    from database.audio import audio_downloaded, save_audio_downloaded
                    audio_data = {
                        "message_id": sent_audio.message_id,
                        "chat_id": original_message.chat.id,
                        "file_id": sent_audio.audio.file_id
                    }
                    audio_downloaded[audio_id] = audio_data
                    await save_audio_downloaded(audio_id, audio_data)

                    # Удаляем временный аудио файл
                    try:
                        os.remove(audio_path)
                    except:
                        pass
                else:
                    await msg.edit_text("❌ Не удалось извлечь аудио из видео")
            except Exception as e:
                logger.error(f"❌ Ошибка извлечения аудио: {e}")
                await msg.edit_text("❌ Не удалось извлечь аудио из видео")

            await msg.delete()
            return

    # 3. Динамическая клавиатура
    from database.db import get_media_cache
    cached = await get_media_cache(clean_url)
    url_hash = hashlib.md5(clean_url.encode()).hexdigest()[:16]

    if cached and cached[1]:
        # Аудио уже в кэше - показываем "Установленное аудио"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎵 Установленное аудио", callback_data=secure_callback(f"send_cached_audio:{url_hash}"))],
            [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_video:{user_id}"))]
        ])
    else:
        # Аудио нет в кэше - показываем "Скачать аудио"
        from utils.crypto import secure_callback
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📥 Скачать аудио", callback_data=secure_callback(f"dl_audio:{clean_url[:30]}"))],
            [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_video:{user_id}"))]
        ])

    # 4. Отправляем видео напрямую по ID или из файла
    if is_trashed and video_file_id:
        sent_video = await bot.send_video(original_message.chat.id, video_file_id, caption=caption_text, reply_markup=keyboard)
    else:
        sent_video = await bot.send_video(original_message.chat.id, FSInputFile(content_path), caption=caption_text, reply_markup=keyboard)

    # Синхронизация логики старого кэша
    video_data = {
        "url": clean_url,
        "video_message_id": sent_video.message_id,
        "chat_id": original_message.chat.id,
        "owner_id": user_id  # Сохраняем ID владельца видео
    }
    audio_url_storage[url_id] = video_data
    await save_audio_url_storage(url_id, video_data)

    # Запускаем фоновое извлечение аудио если его нет в кэше
    if not (cached and cached[1]) and video_file_id and size_mb <= MAX_UPLOAD_SIZE_MB:
        if DEBUG_MODE:
            logger.info(f"🔄 Запуск фоновой задачи извлечения аудио для TikTok")
        import tempfile
        import shutil
        import asyncio

        # Копируем видео во временное место
        temp_video = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(content_path)[1])
        temp_video.close()
        shutil.copy2(content_path, temp_video.name)

        temp_audio = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
        temp_audio.close()

        # Импортируем функцию из download_video.py
        from handlers.commands.download_video import background_extract_audio

        asyncio.create_task(background_extract_audio(
            file_path=temp_video.name,
            audio_path=temp_audio.name,
            clean_url=clean_url,
            video_file_id=video_file_id,
            bot=bot,
            chat_id=original_message.chat.id,
            video_message_id=sent_video.message_id,
            video_title="Аудио"
        ))

    await msg.delete()

    # Удаляем временный файл
    try:
        os.remove(content_path)
    except Exception:
        pass


# ==================== ОБРАБОТЧИК СОЦСЕТЕЙ (X, Reddit, Facebook) ====================

@router.message(F.text & ~F.text.startswith('/'), has_social_url)
async def handle_social_message(message: types.Message, bot: Bot):
    """Обработчик ссылок из X/Twitter, Reddit, Facebook"""
    await ruser(message.from_user)
    
    user_id = message.from_user.id

    # Проверяем лимит
    if not await check_and_increment_usage(user_id):
        limit_data = await get_user_limit(user_id)
        if limit_data:
            max_uses = limit_data[0]

            if max_uses == 0:
                await message.reply("🚫 Использование бота для вас заблокировано.")
                try:
                    await message.delete()
                except:
                    pass
                return

            from datetime import datetime, timezone, timedelta
            utc_plus_1 = timezone(timedelta(hours=1))
            current_time = datetime.now(utc_plus_1)
            midnight = (current_time + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            time_diff = midnight - current_time

            hours = time_diff.seconds // 3600
            minutes = (time_diff.seconds % 3600) // 60

            reset_time_str = midnight.strftime("%H:%M")
            countdown = f"{hours}ч {minutes}м"

            await message.reply(
                f"❌ Вы превысили дневной лимит использований ({max_uses} раз)\n"
                f"⏰ Сброс в {reset_time_str} UTC+1 (через {countdown})"
            )

            # Удаляем сообщение пользователя
            try:
                await message.delete()
            except:
                pass
        return
    
    # Проверяем whitelist
    if message.chat.type in ['group', 'supergroup']:
        if not await is_group_whitelisted(message.chat.id):
            await message.reply("❌ Бот не может работать в этой группе")
            return
    else:
        if not await is_user_whitelisted(message.from_user.id):
            await message.reply("❌ У вас нет доступа к этому боту")
            return
    
    # Извлекаем URL
    url = extract_social_url(message.text)
    if not url:
        return
    
    url = clean_social_url(url)
    platform = get_platform_name(url)
    platform_name = get_platform_display_name(platform)
    platform_emoji = get_platform_emoji(platform)
    
    user_link = get_user_link(message.from_user)
    username_display = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    
    logger.info(f"📥 {user_link}: ссылка {platform_name} — {url}")
    
    msg = await message.reply(f"⏳ Скачиваю из {platform_name}...")
    
    try:
        content_path, content_type = await download_social_content(url)
        
        if not content_path:
            _err_ctx = f"не удалось скачать контент из {platform_name}"
            # Error logged
            _err_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_error:{user_id}"))]
            ])
            await msg.edit_text("❌ Произошла ошибка при обработке запроса", reply_markup=_err_keyboard)
            # Откатываем счётчик
            limit_data = await get_user_limit(user_id)
            if limit_data:
                await decrement_usage(user_id)
            return
        
        caption_text = f"{platform_emoji} {username_display}\n{url}"
        
        if content_type == "video":
            size_mb = os.path.getsize(content_path) / (1024 * 1024)
            
            if size_mb > MAX_UPLOAD_SIZE_MB:
                await msg.edit_text(f"❌ Видео слишком большое ({size_mb:.0f}MB). Лимит: {MAX_UPLOAD_SIZE_MB}MB")
                try:
                    os.remove(content_path)
                except:
                    pass
                return

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_video:{user_id}"))]
            ])
            
            video_file = FSInputFile(content_path)
            await bot.send_video(
                chat_id=message.chat.id,
                video=video_file,
                caption=caption_text,
                reply_markup=keyboard
            )
            
            logger.info(f"✅ {user_link} - {platform_name} видео ({size_mb:.1f}MB)")
            await msg.delete()
            
            try:
                os.remove(content_path)
            except:
                pass
                
        elif content_type == "images":
            # Отправляем как медиа-группу
            if len(content_path) == 1:
                # Одно фото — отправляем отдельно
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_video:{user_id}"))]
                ])
                await bot.send_photo(
                    chat_id=message.chat.id,
                    photo=FSInputFile(content_path[0]),
                    caption=caption_text,
                    reply_markup=keyboard
                )
            else:
                # Несколько фото — медиа-группа
                media_group = []
                for i, photo_path in enumerate(content_path[:10]):
                    if i == len(content_path[:10]) - 1:
                        media_group.append(InputMediaPhoto(
                            media=FSInputFile(photo_path),
                            caption=caption_text
                        ))
                    else:
                        media_group.append(InputMediaPhoto(media=FSInputFile(photo_path)))
                
                await bot.send_media_group(
                    chat_id=message.chat.id,
                    media=media_group
                )
            
            logger.info(f"✅ {user_link} - {platform_name} {len(content_path)} фото")
            await msg.delete()
            
            # Удаляем временные файлы
            for photo_path in content_path:
                try:
                    os.remove(photo_path)
                except:
                    pass
            try:
                parent_dir = os.path.dirname(content_path[0])
                os.rmdir(parent_dir)
            except:
                pass
        
        # Удаляем исходное сообщение
        try:
            await message.delete()
        except:
            pass
            
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке {platform_name}: {str(e)}")
        _err_ctx = f"ошибка при обработке {platform_name}: {str(e)[:60]}"
        # Error logged
        _err_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_error:{user_id}"))]
        ])
        await msg.edit_text("❌ Произошла ошибка при обработке запроса", reply_markup=_err_keyboard)
        limit_data = await get_user_limit(user_id)
        if limit_data:
            await decrement_usage(user_id)


def register_message_handlers(dp):
    """Регистрация обработчиков сообщений"""
    dp.include_router(router)
