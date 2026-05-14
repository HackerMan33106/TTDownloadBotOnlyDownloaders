"""
Команда /dw - загрузка видео с различных платформ
"""
import os
import re
import shutil
import tempfile
import math
import asyncio
import subprocess
import hashlib
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, InputMediaVideo, InputMediaPhoto
from aiogram.filters import Command

from services.downloaders.youtube import YouTubeDownloader
from services.downloaders.facebook import FacebookDownloader
from services.downloaders.reddit import RedditDownloader
from services.downloaders.twitter import TwitterDownloader
from services.downloaders.bilibili import BilibiliDownloader
from services.downloaders.rutube import RutubeDownloader
from services.downloaders.soundcloud import SoundCloudDownloader
from services.downloaders.dzen import DzenDownloader
from services.downloaders.instagram import InstagramDownloader
from services.downloaders.pornhub import PornHubDownloader
from config.settings import DEBUG_MODE, logger, MAX_UPLOAD_SIZE_MB
from utils.helpers import create_delete_button, create_media_caption

from utils.progress import DownloadProgress
from services.downloaders.base import is_safe_url
from database.audio import audio_downloaded

router = Router(name="download_video")


def clean_url(url: str) -> str:
    """Очищает URL, сохраняя важные query параметры для платформ, которым они нужны"""
    try:
        parsed = urlparse(url)

        # Для SoundCloud коротких ссылок (on.soundcloud.com) получаем полный URL
        if 'on.soundcloud.com' in parsed.netloc.lower():
            try:
                import yt_dlp
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info and 'webpage_url' in info:
                        url = info['webpage_url']
                        parsed = urlparse(url)
                        logger.info(f"🔗 Короткая ссылка развернута: {url}")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось развернуть короткую ссылку SoundCloud: {e}")

        # Для YouTube оставляем только параметр v= (ID видео)
        if 'youtube.com' in parsed.netloc.lower() or 'youtu.be' in parsed.netloc.lower():
            if 'youtube.com' in parsed.netloc.lower() and parsed.path == '/watch':
                # Извлекаем только v= параметр
                from urllib.parse import parse_qs
                query_params = parse_qs(parsed.query)
                video_id = query_params.get('v', [None])[0]
                if not video_id:
                    return ''
                clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', f'v={video_id}', ''))
                return clean
            elif 'youtu.be' in parsed.netloc.lower():
                # Для коротких ссылок youtu.be убираем query полностью
                clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
                return clean

        # Платформы, которым нужны query параметры
        keep_query_domains = ['music.youtube.com', 'spotify.com', 'facebook.com']

        needs_query = any(domain in parsed.netloc.lower() for domain in keep_query_domains)

        if needs_query:
            # Сохраняем query, убираем только fragment
            clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ''))
        else:
            # Для остальных платформ убираем query и fragment
            clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
        return clean
    except Exception as e:
        logger.warning(f"⚠️ Ошибка очистки URL: {e}")
        return url

# Инициализируем все загрузчики
DOWNLOADERS = [
    YouTubeDownloader(),
    FacebookDownloader(),
    RedditDownloader(),
    TwitterDownloader(),
    BilibiliDownloader(),
    # VKDownloader(),  # Убран из списка
    RutubeDownloader(),
    # SpotifyDownloader(),  # Временно отключен
    SoundCloudDownloader(),
    # PornHubDownloader(),  # Не работает (таймауты, 404)
    DzenDownloader(),
    InstagramDownloader(),
]





async def get_video_keyboard(url: str, is_music: bool = False, original_msg_id: int = None) -> InlineKeyboardMarkup:
    """Создает клавиатуру с кнопками для видео"""
    from database.db import get_media_cache
    from utils.crypto import secure_callback
    buttons = []
    if not is_music:
        clean = clean_url(url)
        cached = await get_media_cache(clean)

        url_hash = hashlib.md5(clean.encode()).hexdigest()[:16]
        if cached and cached[1]:
            # Если аудио есть в кэше - показываем кнопку "Установленное аудио"
            buttons.append([InlineKeyboardButton(text="🎵 Установленное аудио", callback_data=secure_callback(f"send_cached_audio:{url_hash}"))])
        else:
            # Если аудио нет в кэше - показываем кнопку "Скачать аудио"
            short_url = url[:30] if len(url) > 30 else url
            callback_data = f"dl_audio:{short_url}"
            if original_msg_id:
                callback_data += f":{original_msg_id}"
            buttons.append([InlineKeyboardButton(text="📥 Скачать аудио", callback_data=secure_callback(callback_data))])

    delete_data = "delete_message"
    if original_msg_id:
        delete_data = f"delete_message:{original_msg_id}"
    buttons.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=secure_callback(delete_data))])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def split_file(file_path: str, chunk_size_mb: int = 45) -> list[str]:
    """Разделяет ВИДЕО файл на части по chunk_size_mb МБ используя ffmpeg"""
    file_size = os.path.getsize(file_path)
    chunk_size = chunk_size_mb * 1024 * 1024
    
    if file_size <= chunk_size:
        return [file_path]
    
    file_ext = os.path.splitext(file_path)[1].lower()
    
    # Для видео используем ffmpeg
    if file_ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv']:
        # Получаем длительность видео и битрейт
        try:
            probe_cmd = [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration,size',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                file_path
            ]
            process = await asyncio.create_subprocess_exec(
                *probe_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
            lines = stdout.decode('utf-8').strip().split('\n')
            total_duration = float(lines[0])
            total_size = int(lines[1])
        except:
            return [file_path]
        
        # Вычисляем битрейт и длительность сегмента
        bitrate = (total_size * 8) / total_duration  # bits per second
        # 90% от лимита для подстраховки
        target_size = int(MAX_UPLOAD_SIZE_MB * 0.9) * 1024 * 1024
        segment_duration = (target_size * 8) / bitrate  # секунды
        
        # Проверяем сколько частей получится
        num_parts = math.ceil(total_duration / segment_duration)
        
        logger.info(f"📹 Разделение видео: размер={total_size/(1024*1024):.1f}MB, длительность={total_duration:.1f}s, битрейт={bitrate/1000:.0f}kbps")
        logger.info(f"📦 План: {num_parts} частей по ~{segment_duration:.1f}s (~{target_size/(1024*1024):.1f}MB каждая)")
        
        parts = []
        file_dir = os.path.dirname(file_path)
        file_name = os.path.basename(file_path)
        base_name, ext = os.path.splitext(file_name)
        
        # Разделяем видео на части
        for i in range(num_parts):
            start_time = i * segment_duration
            part_path = os.path.join(file_dir, f"{base_name}_part{i+1}{ext}")
            
            cmd = [
                'ffmpeg',
                '-i', file_path,
                '-ss', str(start_time),
                '-t', str(segment_duration),
                '-c', 'copy',
                '-avoid_negative_ts', '1',
                '-y',
                part_path
            ]
            
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await asyncio.wait_for(process.communicate(), timeout=120)
                if os.path.exists(part_path):
                    part_size = os.path.getsize(part_path)
                    
                    # Если часть всё равно превышает лимит, уменьшаем битрейт
                    if part_size > MAX_UPLOAD_SIZE_MB * 1024 * 1024:
                        logger.warning(f"⚠️ Часть {i+1} превышает {MAX_UPLOAD_SIZE_MB}MB ({part_size/(1024*1024):.1f}MB), перекодируем...")
                        os.remove(part_path)
                        cmd_reenc = [
                            'ffmpeg',
                            '-i', file_path,
                            '-ss', str(start_time),
                            '-t', str(segment_duration),
                            '-c:v', 'libx264',
                            '-crf', '28',
                            '-preset', 'faster',
                            '-c:a', 'aac',
                            '-b:a', '128k',
                            '-y',
                            part_path
                        ]
                        process_reenc = await asyncio.create_subprocess_exec(
                            *cmd_reenc,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        await asyncio.wait_for(process_reenc.communicate(), timeout=180)

                        if os.path.exists(part_path):
                            new_size = os.path.getsize(part_path)
                            logger.info(f"✅ Перекодировано: {new_size/(1024*1024):.1f}MB")
                    
                    if os.path.exists(part_path) and os.path.getsize(part_path) > 0:
                        parts.append(part_path)
            except Exception as e:
                logger.error(f"❌ Ошибка разделения части {i+1}: {e}")
                continue
        
        return parts if parts else [file_path]
    else:
        # Для аудио и других файлов используем бинарное разделение
        return split_file_binary(file_path, chunk_size_mb)


def split_file_binary(file_path: str, chunk_size_mb: int = 45) -> list[str]:
    """Старый метод - разделяет файл на бинарные части (для не-видео файлов)"""
    chunk_size = chunk_size_mb * 1024 * 1024
    file_size = os.path.getsize(file_path)
    
    if file_size <= chunk_size:
        return [file_path]
    
    parts = []
    file_dir = os.path.dirname(file_path)
    file_name = os.path.basename(file_path)
    base_name, ext = os.path.splitext(file_name)
    
    with open(file_path, 'rb') as f:
        part_num = 1
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            
            part_path = os.path.join(file_dir, f"{base_name}.part{part_num}{ext}")
            with open(part_path, 'wb') as part_file:
                part_file.write(chunk)
            
            parts.append(part_path)
            part_num += 1
    
    return parts


def find_downloader(url: str):
    """Находит подходящий загрузчик для URL"""
    for downloader in DOWNLOADERS:
        if downloader.can_handle(url):
            return downloader
    return None


def extract_urls(text: str) -> list[str]:
    """Извлекает все URL из текста"""
    # Регулярное выражение для поиска URLs
    url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    urls = re.findall(url_pattern, text)
    return urls


async def extract_audio_simple(video_path: str, audio_path: str, status_msg: Message = None, url_info: str = "") -> bool:
    """Извлекает аудио из видео (простой вариант без прогресс-бара)"""
    import subprocess
    import time
    import re

    # Получаем длительность видео для оценки времени
    try:
        probe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
        process = await asyncio.create_subprocess_exec(
            *probe_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        total_duration = float(stdout.decode('utf-8').strip())
        # Примерная оценка: 1 секунда видео = 0.1 секунды обработки
        estimated_time = int(total_duration * 0.1)
        duration_text = f"\n⏱ Примерное время: ~{estimated_time}s"
    except:
        total_duration = None
        duration_text = ""

    if status_msg:
        try:
            await status_msg.edit_text(f"🎵 {url_info}Извлечение аудио...{duration_text}")
        except:
            pass

    try:
        # Команда ffmpeg с выводом прогресса в stderr
        # Используем libmp3lame с битрейтом 192k для быстрого кодирования
        cmd = [
            'ffmpeg', '-i', video_path,
            '-vn',  # Без видео
            '-acodec', 'libmp3lame',
            '-b:a', '192k',
            '-map_metadata', '-1',  # Удаляем все метаданные
            '-progress', 'pipe:2',  # Прогресс в stderr
            audio_path, '-y'
        ]

        start_time = time.time()
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # Мониторим stderr для логирования прогресса
        last_log_time = start_time

        def monitor_progress():
            nonlocal last_log_time
            current_time = 0

            for line in process.stderr:
                # Ищем строку с временем обработки
                if line.startswith('out_time='):
                    try:
                        # Формат: out_time=00:01:23.456789
                        time_str = line.split('=')[1].strip()
                        parts = time_str.split(':')
                        hours = int(parts[0])
                        minutes = int(parts[1])
                        seconds = float(parts[2])
                        current_time = hours * 3600 + minutes * 60 + seconds

                        # Логируем каждые 10 секунд
                        now = time.time()
                        if now - last_log_time >= 10:
                            elapsed = now - start_time
                            if total_duration:
                                progress_percent = (current_time / total_duration) * 100
                                logger.info(f"🎵 FFmpeg прогресс: {int(current_time)}s / {int(total_duration)}s ({progress_percent:.1f}%) | Прошло: {int(elapsed)}s")
                            else:
                                logger.info(f"🎵 FFmpeg прогресс: {int(current_time)}s обработано | Прошло: {int(elapsed)}s")
                            last_log_time = now
                    except:
                        pass

        # Запускаем мониторинг в фоне
        import asyncio
        monitor_task = asyncio.create_task(asyncio.to_thread(monitor_progress))

        # Ждём завершения с таймаутом
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=600)
        except asyncio.TimeoutError:
            logger.error(f"❌ Таймаут извлечения аудио (>600s)")
            process.kill()
            return False

        elapsed = time.time() - start_time
        success = process.returncode == 0 and os.path.exists(audio_path)

        if success and DEBUG_MODE:
            logger.info(f"✅ Аудио извлечено за {int(elapsed)}s: {os.path.getsize(audio_path)/(1024*1024):.1f}MB")
        elif not success:
            if process.returncode != 0:
                logger.error(f"❌ Ошибка извлечения аудио (код: {process.returncode})")
            else:
                logger.error(f"❌ Файл аудио не создан: {audio_path} (код: {process.returncode})")

        return success

    except Exception as e:
        logger.error(f"❌ Ошибка извлечения аудио: {e}")
        return False


async def background_extract_audio(file_path: str, audio_path: str, clean_url: str, video_file_id: str, bot, chat_id: int = None, video_message_id: int = None, video_title: str = None, user_id: int = None):
    """Фоновая задача для извлечения и кэширования аудио"""
    try:
        from database.db import set_media_cache
        from config.settings import TRASH_GROUP_ID
        from utils.crypto import secure_callback
        import hashlib

        if DEBUG_MODE:
            logger.info(f"🔄 Фоновое извлечение аудио для {clean_url}")

        # Извлекаем аудио без прогресс-бара (фоновый режим)
        success = await extract_audio_simple(file_path, audio_path, status_msg=None)

        if success and os.path.exists(audio_path):
            # Отправляем аудио в мусорную группу для кэширования
            if TRASH_GROUP_ID:
                try:
                    # Создаём кнопку для удаления из кэша
                    url_hash = hashlib.md5(clean_url.encode()).hexdigest()[:16]
                    cache_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🗑️ Удалить из кэша", callback_data=secure_callback(f"clear_cache:{url_hash}"))]
                    ])

                    a_msg = await bot.send_audio(
                        TRASH_GROUP_ID,
                        FSInputFile(audio_path),
                        title=video_title if video_title else "Аудио",
                        caption=clean_url,
                        reply_markup=cache_keyboard
                    )
                    audio_file_id = a_msg.audio.file_id

                    # Обновляем кэш
                    await set_media_cache(clean_url, video_file_id, audio_file_id)
                    if DEBUG_MODE:
                        logger.info(f"✅ Фоновое кэширование аудио завершено для {clean_url}")

                    # Обновляем кнопку на видео с "Скачать аудио" на "Установленное аудио"
                    if chat_id and video_message_id:
                        try:
                            # Получаем текущую клавиатуру видео
                            from aiogram.exceptions import TelegramBadRequest

                            # Создаем новую клавиатуру с кнопкой "Установленное аудио"
                            new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="🎵 Установленное аудио", callback_data=secure_callback(f"send_cached_audio:{url_hash}"))],
                                [InlineKeyboardButton(text="🗑 Удалить", callback_data=secure_callback(f"delete_video:{user_id}") if user_id else secure_callback("delete_message"))]
                            ])

                            await bot.edit_message_reply_markup(
                                chat_id=chat_id,
                                message_id=video_message_id,
                                reply_markup=new_keyboard
                            )
                            if DEBUG_MODE:
                                logger.info(f"✅ Кнопка обновлена на 'Установленное аудио' после фонового кэширования")
                        except TelegramBadRequest as e:
                            logger.warning(f"⚠️ Не удалось обновить кнопку видео: {e}")
                        except Exception as e:
                            logger.error(f"❌ Ошибка обновления кнопки видео: {e}")

                except Exception as e:
                    logger.error(f"❌ Ошибка отправки аудио в мусорную группу: {e}")
        else:
            logger.warning(f"⚠️ Фоновое извлечение аудио не удалось для {clean_url}")

    except Exception as e:
        logger.error(f"❌ Ошибка фоновой задачи извлечения аудио: {e}")
    finally:
        # Удаляем временные файлы
        try:
            if os.path.exists(audio_path):
                os.unlink(audio_path)
        except:
            pass
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
        except:
            pass


async def process_single_url(message: Message, url: str, original_msg_id: int = None, status_msg: Message = None, url_index: int = None, total_urls: int = None, only_audio: bool = False) -> bool:
    """Обрабатывает загрузку одного URL. Возвращает True если успешно, False если ошибка"""
    # Проверка URL на SSRF (запрет internal/private IP)
    if not is_safe_url(url):
        err_kb = create_delete_button(message)
        if status_msg:
            await status_msg.edit_text("❌ Недопустимый URL", reply_markup=err_kb)
        else:
            await message.answer("❌ Недопустимый URL", reply_markup=err_kb)
        return False

    # Находим подходящий загрузчик
    downloader = find_downloader(url)

    # Для музыкальных сервисов автоматически включаем режим только аудио
    if downloader and isinstance(downloader, SoundCloudDownloader):
        only_audio = True
        logger.info(f"🎵 Музыкальный сервис обнаружен, включен режим only_audio")

    from database.db import get_media_cache, set_media_cache
    from config.settings import TRASH_GROUP_ID

    clean = clean_url(url)
    logger.info(f"🔗 URL очищен: {url} -> {clean}")
    cached = await get_media_cache(clean)
    logger.info(f"🔍 Проверка кэша для '{clean}': {cached}")

    if cached:
        video_id, audio_id = cached
        logger.info(f"📦 Кэш найден: video_id={video_id is not None}, audio_id={audio_id is not None}, only_audio={only_audio}")
        if only_audio:
            if audio_id:
                # Создаем кнопку удаления для аудио из кэша
                import time
                audio_id_key = f"audio_only_{message.from_user.id}_{int(time.time())}"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{message.from_user.id}:{audio_id_key}"))]
                ])

                # Создаем caption для аудио через атрибут -a (без названия видео)
                caption = create_media_caption(message.from_user, url=clean, media_type="audio", title=None)

                sent_audio = await message.bot.send_audio(message.chat.id, audio_id, caption=caption, reply_markup=keyboard, title="")

                # Сохраняем информацию об аудио для возможности удаления
                from database.audio import audio_downloaded, save_audio_downloaded
                audio_data = {
                    "message_id": sent_audio.message_id,
                    "chat_id": message.chat.id,
                    "file_id": sent_audio.audio.file_id if sent_audio.audio else None
                }
                audio_downloaded[audio_id_key] = audio_data
                await save_audio_downloaded(audio_id_key, audio_data)

                if status_msg:
                    await status_msg.delete()
                return True
            else:
                # Аудио нет в кэше, но видео есть - попробуем извлечь аудио из видео
                logger.info(f"🎵 Аудио отсутствует в кэше, попытка извлечь из видео {video_id}")
                # Продолжаем выполнение функции для извлечения аудио
        else:
            if video_id:
                # Используем очищенный URL для кэшированного видео
                caption = create_media_caption(
                    message.from_user,
                    url=clean,
                    media_type="video",
                    title="Видео"
                )

                kb = await get_video_keyboard(url, False, original_msg_id)
                await message.bot.send_video(message.chat.id, video_id, caption=caption, reply_markup=kb)
                if status_msg:
                    await status_msg.delete()
                return True
                return True

    if not downloader:
        error_text = (
            "❌ Ошибка\n\n"
            "Не удалось определить платформу или платформа не поддерживается.\n"
            "Используйте /dw без аргументов для списка поддерживаемых платформ."
        )
        err_kb = create_delete_button(message)
        if status_msg:
            await status_msg.edit_text(error_text, reply_markup=err_kb)
        else:
            await message.answer(error_text, reply_markup=err_kb)
        return False
    
    # Создаем временную директорию
    temp_dir = tempfile.mkdtemp()
    local_status_msg = status_msg
    
    try:
        # Обновляем или создаем статус
        url_info = ""
        if url_index is not None and total_urls is not None and total_urls > 1:
            url_info = f"[{url_index}/{total_urls}] "
        
        status_text = (
            f"⏳ {url_info}Подключение...\n\n"
            f"Платформа: {downloader.name}\n"
            f"URL: {url}"
        )
        if local_status_msg:
            await local_status_msg.edit_text(status_text)
        else:
            local_status_msg = await message.answer(status_text)
        
        # Создаём объект прогресса и запускаем загрузку в executor с updater loop
        progress = DownloadProgress(url, downloader.name)
        hook = progress.make_progress_hook()
        
        loop = asyncio.get_running_loop()
        
        # Синхронная обёртка для вызова async download() в отдельном потоке
        def _sync_download():
            return asyncio.run(downloader.download(url, temp_dir, hook))
        
        # Запускаем загрузку в отдельном потоке (yt-dlp блокирующий)
        download_task = loop.run_in_executor(None, _sync_download)
        
        # Async updater loop — обновляем сообщение раз в 3 секунды
        last_text = ""
        while not download_task.done():
            await asyncio.sleep(3)
            if download_task.done():
                break
            new_text = progress.format_status_text(url_info)
            if new_text != last_text:
                try:
                    await local_status_msg.edit_text(new_text)
                    last_text = new_text
                except Exception:
                    pass  # Telegram 429 или другие ошибки — просто пропускаем
        
        result = await download_task
        
        # Финальное обновление если была ошибка от yt-dlp
        if progress.error:
            logger.error(f"Progress hook error: {progress.error}")
        
        if not result:
            _err_ctx = f"не удалось загрузить видео с {downloader.name}"
            # Error logged
            _err_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data="delete_message")]
            ])
            await local_status_msg.edit_text(
                "❌ Произошла ошибка при обработке запроса",
                reply_markup=_err_kb
            )
            return False
        
        file_path = result['file_path']
        file_size = os.path.getsize(file_path)
        file_ext = os.path.splitext(file_path)[1].lower()
        
        # Определяем, музыкальный ли это сервис
        is_music = isinstance(downloader, (SoundCloudDownloader,))

        # Проверяем размер и разделяем если нужно
        upload_limit = int(MAX_UPLOAD_SIZE_MB * 0.95) * 1024 * 1024  # 95% от лимита

        # Устанавливаем аудио/видео в TRASH_GROUP_ID для кэширования (если размер не превышает)
        video_file_id, audio_file_id = None, None

        # Проверяем существующий кэш
        existing_cache = await get_media_cache(clean)
        if existing_cache:
            video_file_id, audio_file_id = existing_cache
            logger.info(f"📦 Найден существующий кэш: video_id={video_file_id is not None}, audio_id={audio_file_id is not None}")

        # Сначала отправляем видео в мусорную группу для кэширования (быстро)
        if TRASH_GROUP_ID and file_size <= upload_limit:
            try:
                if is_music:
                    if not audio_file_id:
                        from utils.crypto import secure_callback

                        # Создаём кнопку для удаления из кэша
                        url_hash = hashlib.md5(clean.encode()).hexdigest()[:16]
                        cache_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🗑️ Удалить из кэша", callback_data=secure_callback(f"clear_cache:{url_hash}"))]
                        ])

                        a_msg = await message.bot.send_audio(
                            TRASH_GROUP_ID,
                            FSInputFile(file_path),
                            caption=clean,
                            reply_markup=cache_keyboard
                        )
                        audio_file_id = a_msg.audio.file_id
                        logger.info(f"📤 Аудио отправлено в мусорную группу с кнопкой удаления")
                        await set_media_cache(clean, None, audio_file_id)
                else:
                    # Отправляем видео только если его нет в кэше
                    if not video_file_id:
                        from utils.crypto import secure_callback

                        # Создаём кнопку для удаления из кэша
                        url_hash = hashlib.md5(clean.encode()).hexdigest()[:16]
                        cache_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🗑️ Удалить из кэша", callback_data=secure_callback(f"clear_cache:{url_hash}"))]
                        ])

                        v_msg = await message.bot.send_video(
                            TRASH_GROUP_ID,
                            FSInputFile(file_path),
                            caption=clean,
                            reply_markup=cache_keyboard
                        )
                        video_file_id = v_msg.video.file_id
                        logger.info(f"📤 Видео отправлено в мусорную группу")
                        # Сохраняем кэш с video_id сразу
                        await set_media_cache(clean, video_file_id, None)
            except Exception as e:
                logger.error(f"Failed to trash cache for {clean}: {e}")

        # Теперь извлекаем аудио только если запрошено (-a флаг)
        audio_extracted = False
        audio_path = file_path.rsplit('.', 1)[0] + '.mp3'
        if not is_music and file_size <= upload_limit and not audio_file_id and only_audio:
            if DEBUG_MODE:
                logger.info(f"🎵 Начало извлечения аудио из {file_size/(1024*1024):.1f}MB видео")
            audio_extracted = await extract_audio_simple(file_path, audio_path, local_status_msg, url_info)

            # Отправляем извлечённое аудио в мусорную группу
            if audio_extracted and TRASH_GROUP_ID:
                try:
                    from utils.crypto import secure_callback

                    # Создаём кнопку для удаления из кэша с защитой
                    url_hash = hashlib.md5(clean.encode()).hexdigest()[:16]
                    cache_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🗑️ Удалить из кэша", callback_data=secure_callback(f"clear_cache:{url_hash}"))]
                    ])

                    a_msg = await message.bot.send_audio(
                        TRASH_GROUP_ID,
                        FSInputFile(audio_path),
                        title=result.get('title', 'Аудио'),
                        caption=clean,
                        reply_markup=cache_keyboard
                    )
                    audio_file_id = a_msg.audio.file_id
                    logger.info(f"📤 Аудио отправлено в мусорную группу с кнопкой удаления")
                    # Обновляем кэш с audio_id
                    await set_media_cache(clean, video_file_id, audio_file_id)
                    logger.info(f"💾 Кэш обновлён: video_id={video_file_id is not None}, audio_id={audio_file_id is not None}")
                except Exception as e:
                    logger.error(f"Failed to cache audio for {clean}: {e}")

        # Если мы хотим только аудио, отправляем его и завершаем
        if only_audio:
            # Создаем кнопку удаления для аудио
            import time
            audio_id = f"audio_only_{message.from_user.id}_{int(time.time())}"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{message.from_user.id}:{audio_id}"))]
            ])

            # Создаем caption для аудио через атрибут -a (без названия видео)
            caption = create_media_caption(message.from_user, url=clean, media_type="audio", title=None)

            sent_audio = None
            if audio_file_id:
                if DEBUG_MODE:
                    logger.info(f"📤 Отправка аудио из кэша (file_id)")
                sent_audio = await message.bot.send_audio(message.chat.id, audio_file_id, caption=caption, reply_markup=keyboard)
            elif is_music:
                logger.info(f"📤 Отправка музыкального файла")
                sent_audio = await message.bot.send_audio(message.chat.id, FSInputFile(file_path), caption=caption, reply_markup=keyboard, title="")
            elif audio_extracted and os.path.exists(audio_path):
                if DEBUG_MODE:
                    logger.info(f"📤 Отправка извлечённого аудио ({os.path.getsize(audio_path)/(1024*1024):.1f}MB)")
                sent_audio = await message.bot.send_audio(message.chat.id, FSInputFile(audio_path), caption=caption, reply_markup=keyboard, title="")
            else:
                error_msg = "❌ Не удалось извлечь аудио из видео"
                if not audio_extracted:
                    error_msg += "\n\nВозможные причины:\n• Видео слишком большое\n• Таймаут извлечения\n• Отсутствует аудиодорожка"
                await local_status_msg.edit_text(error_msg)
                return False

            # Сохраняем информацию об аудио для возможности удаления
            if sent_audio:
                from database.audio import audio_downloaded, save_audio_downloaded
                audio_data = {
                    "message_id": sent_audio.message_id,
                    "chat_id": message.chat.id,
                    "file_id": sent_audio.audio.file_id if sent_audio.audio else None
                }
                audio_downloaded[audio_id] = audio_data
                await save_audio_downloaded(audio_id, audio_data)

            await local_status_msg.delete()
            return True

        # Проверяем размер и разделяем если нужно
        if file_size > upload_limit:
            # Разделяем на части
            await local_status_msg.edit_text(
                f"📦 {url_info}Разделение файла...\n\n"
                f"Размер файла: {file_size / (1024*1024):.1f} MB\n"
                f"Файл будет разделен на части"
            )

            parts = await split_file(file_path)
            total_parts = len(parts)
            
            logger.info(f"📦 Файл разделен на {total_parts} частей")
            
            # Проверяем, что все части меньше лимита
            valid_parts = []
            for i, part in enumerate(parts, 1):
                part_size = os.path.getsize(part)
                size_mb = part_size / (1024*1024)
                if part_size > MAX_UPLOAD_SIZE_MB * 1024 * 1024:
                    logger.warning(f"⚠️ Часть {i}/{total_parts} слишком большая ({size_mb:.1f}MB), пропускаем")
                    continue
                logger.info(f"✅ Часть {i}/{total_parts}: {size_mb:.1f}MB - OK")
                valid_parts.append(part)
            
            if not valid_parts:
                await local_status_msg.edit_text(
                    f"❌ {url_info}Не удалось разделить файл\n\n"
                    f"Все части оказались слишком большими (>{MAX_UPLOAD_SIZE_MB}MB).\n"
                    f"Попробуйте скачать файл через веб-интерфейс.",
                    reply_markup=create_delete_button(message)
                )
                return False
            
            total_parts = len(valid_parts)
            
            # ФИНАЛЬНАЯ проверка размера каждой части перед отправкой
            final_valid_parts = []
            for i, part_path in enumerate(valid_parts, 1):
                part_size = os.path.getsize(part_path)
                size_mb = part_size / (1024*1024)
                
                if part_size > MAX_UPLOAD_SIZE_MB * 1024 * 1024:
                    logger.error(f"❌ Часть {i}/{total_parts} ПРЕВЫШАЕТ ЛИМИТ: {size_mb:.2f}MB > {MAX_UPLOAD_SIZE_MB}MB")
                    continue
                
                final_valid_parts.append(part_path)
            
            if not final_valid_parts:
                logger.error(f"❌ НЕТ ВАЛИДНЫХ ЧАСТЕЙ для отправки!")
                await local_status_msg.edit_text(
                    f"❌ {url_info}Ошибка разделения файла\n\n"
                    f"Не удалось создать части размером <{MAX_UPLOAD_SIZE_MB}MB.\n"
                    f"Исходный файл: {file_size/(1024*1024):.1f}MB",
                    reply_markup=create_delete_button(message)
                )
                return False
            
            total_parts = len(final_valid_parts)
            
            await local_status_msg.edit_text(
                f"📤 {url_info}Отправка частей...\n\n"
                f"Название: {result['title']}\n"
                f"Всего частей: {total_parts}"
            )
            
            # Отправляем части ПО ОДНОЙ (не группой, чтобы избежать лимита на общий размер)
            all_sent_messages = []
            for i, part_path in enumerate(final_valid_parts, 1):
                try:
                    input_file = FSInputFile(part_path, filename=os.path.basename(part_path))
                    
                    # Для последней части добавляем кнопки удаления и скачивания аудио
                    if i == total_parts and len(all_sent_messages) > 0:
                        # Включаем ID всех частей (включая текущую, которую отправляем)
                        all_msg_ids = ",".join([str(msg.message_id) for msg in all_sent_messages])
                        
                        # Добавляем кнопку скачать полное аудио если не музыкальный сервис
                        buttons = []
                        if not is_music:
                            short_url = url[:30] if len(url) > 30 else url
                            buttons.append([InlineKeyboardButton(text="🎵 Скачать полное аудио", callback_data=secure_callback(f"dl_audio:{short_url}"))])
                        buttons.append([InlineKeyboardButton(text="🗑 Удалить все части", callback_data=secure_callback(f"delete_parts:{all_msg_ids}:last"))])
                        
                        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
                        
                        # Добавляем URL в caption для callback
                        username_display = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
                        caption_text = f"🎬 {result['title']} (Часть {i}/{total_parts})\n{username_display}"
                        if not is_music:
                            clean_video_url = clean_url(url)
                            caption_text += f"\n\n{clean_video_url}"
                        
                        sent_msg = await message.answer_video(
                            video=input_file,
                            caption=caption_text,
                            reply_markup=keyboard
                        )
                    else:
                        sent_msg = await message.answer_video(
                            video=input_file,
                            caption=f"🎬 {result['title']} (Часть {i}/{total_parts})"
                        )
                    
                    all_sent_messages.append(sent_msg)
                    logger.info(f"✅ Отправлена часть {i}/{total_parts}")
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки части {i}/{total_parts}: {e}")
            
            # Удаляем статусное сообщение
            try:
                await local_status_msg.delete()
            except:
                pass
            
            logger.info(f"Успешно загружено видео с {downloader.name} (разделено на {total_parts} частей): {url}")
            return True
        
        # Обновляем статус
        await local_status_msg.edit_text(
            f"📤 {url_info}Отправка файла...\n\n"
            f"Название: {result['title']}\n"
            f"Размер: {file_size / (1024*1024):.1f} MB"
        )
        
        # Определяем тип файла
        file_ext = os.path.splitext(file_path)[1].lower()
        
        # Отправляем файл
        input_file = FSInputFile(file_path, filename=os.path.basename(file_path))

        if file_ext in ['.mp3', '.m4a', '.wav', '.flac']:
            # Аудио файл - создаем кнопку удаления
            import time
            audio_id = f"audio_only_{message.from_user.id}_{int(time.time())}"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data=secure_callback(f"delete_audio:{message.from_user.id}:{audio_id}"))]
            ])

            # Создаем caption для музыкального файла (без названия)
            caption = create_media_caption(message.from_user, url=url, media_type="audio", title=None)

            sent_audio = await message.answer_audio(
                audio=input_file,
                caption=caption,
                reply_markup=keyboard,
                title=""
            )

            # Сохраняем информацию об аудио для возможности удаления
            from database.audio import audio_downloaded, save_audio_downloaded
            audio_data = {
                "message_id": sent_audio.message_id,
                "chat_id": message.chat.id,
                "file_id": sent_audio.audio.file_id if sent_audio.audio else None
            }
            audio_downloaded[audio_id] = audio_data
            await save_audio_downloaded(audio_id, audio_data)
        elif file_ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp']:
            # Фото — отправляем как фото
            username_display = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
            caption = f"📷 {username_display}\n\n{url}"
            # Если есть несколько файлов (all_files), отправляем медиа-группу
            all_files = result.get('all_files')
            if all_files and len(all_files) > 1:
                media_group = []
                for i, photo_path in enumerate(all_files[:10]):
                    if i == len(all_files[:10]) - 1:
                        media_group.append(InputMediaPhoto(
                            media=FSInputFile(photo_path),
                            caption=caption
                        ))
                    else:
                        media_group.append(InputMediaPhoto(media=FSInputFile(photo_path)))
                await message.answer_media_group(media=media_group)
            else:
                await message.answer_photo(
                    photo=input_file,
                    caption=caption,
                    reply_markup=create_delete_button(message)
                )
        else:
            # Видео файл - добавляем URL в caption для кнопки "Установленное аудио"
            caption = create_media_caption(
                message.from_user,
                url=clean if not is_music else None,
                media_type="video",
                title=result['title']
            )

            sent_video = await message.answer_video(
                video=input_file,
                caption=caption,
                reply_markup=await get_video_keyboard(url, is_music, original_msg_id)
            )

            # Запускаем фоновое извлечение аудио если его нет в кэше
            if not is_music and not audio_file_id and file_size <= upload_limit:
                if DEBUG_MODE:
                    logger.info(f"🔄 Запуск фоновой задачи извлечения аудио")
                # Копируем видео во временное место, чтобы оно не удалилось
                temp_video = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_path)[1])
                temp_video.close()
                shutil.copy2(file_path, temp_video.name)

                temp_audio = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
                temp_audio.close()

                asyncio.create_task(background_extract_audio(
                    file_path=temp_video.name,
                    audio_path=temp_audio.name,
                    clean_url=clean,
                    video_file_id=video_file_id,
                    bot=message.bot,
                    chat_id=message.chat.id,
                    video_message_id=sent_video.message_id,
                    video_title=result['title'],
                    user_id=message.from_user.id
                ))
        
        # Удаляем статусное сообщение
        try:
            await local_status_msg.delete()
        except:
            pass
        
        logger.info(f"Успешно загружено видео с {downloader.name}: {url}")
        
        # Логируем успешную загрузку
        try:
            from database.whitelist import is_user_whitelisted
            is_wl = await is_user_whitelisted(message.from_user.id) if message.from_user else False
        except:
            is_wl = False
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка при обработке URL {url}: {e}")
        
        # Логируем ошибку загрузки
        platform_name = downloader.name.lower() if downloader else "unknown"
        
        if local_status_msg:
            _err_ctx = f"ошибка загрузки: {str(e)[:80]}"
            # Error logged
            _err_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data="delete_message")]
            ])
            await local_status_msg.edit_text(
                "❌ Произошла ошибка при обработке запроса",
                reply_markup=_err_kb
            )
        else:
            _err_ctx = f"ошибка загрузки: {str(e)[:80]}"
            # Error logged
            _err_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Удалить", callback_data="delete_message")]
            ])
            await message.answer(
                "❌ Произошла ошибка при обработке запроса",
                reply_markup=_err_kb
            )
        return False
    
    finally:
        # Очищаем временную директорию
        try:
            shutil.rmtree(temp_dir)
        except:
            pass


@router.message(Command("dw"))
async def download_video_command(message: Message):
    """Обработчик команды /dw <url> - поддерживает множественные URL и флаг -a для аудио"""

    urls = []
    original_msg_id = None
    only_audio = False

    # Проверяем, если команда - ответ на сообщение
    if message.reply_to_message:
        original_msg_id = message.reply_to_message.message_id
        if message.reply_to_message.text:
            # Ищем все URLs в тексте сообщения, на которое ответили
            urls = extract_urls(message.reply_to_message.text)

    # Если URLs не найдены в ответе, проверяем аргументы команды
    if not urls:
        if not message.text or len(message.text.split()) < 2:
            supported_platforms = []
            for dl in DOWNLOADERS:
                domains = dl.get_supported_domains()
                supported_platforms.extend(domains)

            platforms_text = "\n".join([f"• {platform}" for platform in sorted(set(supported_platforms))])

            await message.answer(
                f"📥 Загрузка видео\n\n"
                f"Использование: /dw <url1> <url2> ...\n"
                f"Или: ответьте командой /dw на сообщение со ссылками\n\n"
                f"💡 Поддерживается загрузка нескольких видео за раз!\n"
                f"💡 Используйте флаг -a (или -ф) для мгновенной выдачи аудио: /dw -a <url>\n\n"
                f"Поддерживаемые платформы:\n{platforms_text}\n\n"
                f"💡 Используйте /dw help для подробной справки",
                reply_markup=create_delete_button(message)
            )
            return

        # Извлекаем аргументы команды
        arg = message.text.split(maxsplit=1)[1].strip()

        # Проверяем на help
        if arg.lower() == 'help':
            supported_platforms = []
            for dl in DOWNLOADERS:
                domains = dl.get_supported_domains()
                supported_platforms.extend(domains)

            platforms_text = "\n".join([f"• {platform}" for platform in sorted(set(supported_platforms))])
            await message.reply(
                f"📚 Подробная справка по /dw\n\n"
                f"🔹 Команда для загрузки видео и аудио с различных платформ\n\n"
                f"📌 Использование:\n"
                f"• /dw <url> - скачать одно видео\n"
                f"• /dw <url1> <url2> ... - скачать несколько видео\n"
                f"• /dw -a <url> (или /dw -ф <url>) - скачать только аудио\n"
                f"• Ответить /dw на сообщение со ссылками\n\n"
                f"⚙️ Возможности:\n"
                f"• Автоматическое разделение больших файлов (>{MAX_UPLOAD_SIZE_MB}MB)\n"
                f"• Скачивание аудио из видео (MP3)\n"
                f"• Скачивание полного аудио из разделённых видео\n"
                f"• Множественная загрузка (несколько URL за раз)\n"
                f"• Кэширование медиафайлов для быстрого доступа\n\n"
                f"🎬 Поддерживаемые платформы:\n{platforms_text}\n\n"
                f"❗ Ограничения:\n"
                f"• Максимальный размер файла: {MAX_UPLOAD_SIZE_MB}MB\n"
                f"• Большие файлы автоматически разделяются",
                reply_markup=create_delete_button(message)
            )
            return

        # Проверяем флаг -a или -ф (русская раскладка)
        if arg.startswith('-a ') or arg.startswith('-ф '):
            only_audio = True
            arg = arg[3:].strip()

        # Извлекаем все URLs из аргументов
        urls = extract_urls(arg)

    if not urls:
        await message.answer(
            "❌ Не найдено ни одной ссылки!\n\n"
            "Используйте: /dw <url> или /dw <url1> <url2> ...\n"
            "Для аудио: /dw -a <url> (или /dw -ф <url>)",
            reply_markup=create_delete_button(message)
        )
        return

    # Удаляем дубликаты URL, сохраняя порядок
    seen = set()
    unique_urls = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    urls = unique_urls
    total_urls = len(urls)

    # Обрабатываем каждый URL
    status_msg = None
    success_count = 0

    for idx, url in enumerate(urls, 1):
        success = await process_single_url(
            message=message,
            url=url,
            original_msg_id=original_msg_id,
            status_msg=status_msg,
            url_index=idx if total_urls > 1 else None,
            total_urls=total_urls if total_urls > 1 else None,
            only_audio=only_audio
        )

        if success:
            success_count += 1

        # Для множественных URL не используем один status_msg, создаем новый каждый раз
        status_msg = None

    # Удаляем оригинальное сообщение только если это сообщение самого пользователя
    if original_msg_id and message.reply_to_message:
        # Проверяем что автор reply_to_message совпадает с автором команды
        if message.reply_to_message.from_user.id == message.from_user.id:
            try:
                await message.bot.delete_message(message.chat.id, original_msg_id)
            except:
                pass

    # Удаляем командное сообщение
    try:
        await message.delete()
    except:
        pass

    # Если было несколько URLs, отправляем итоговое сообщение
    if total_urls > 1:
        summary = await message.answer(
            f"✅ Загрузка завершена!\n\n"
            f"Успешно: {success_count}/{total_urls}",
            reply_markup=create_delete_button(message)
        )
        # Удаляем итоговое сообщение через 5 секунд
        import asyncio
        await asyncio.sleep(5)
        try:
            await summary.delete()
        except:
            pass
