"""
Сервисы скачивания контента из TikTok
"""
import os
import time
import asyncio
import subprocess
import yt_dlp

from config.settings import (
    TEMP_DIR, 
    COOKIES_PATH, 
    MAX_DOWNLOAD_RETRIES, 
    logger
)
from utils.tiktok import is_retryable_error, is_tiktok_slideshow


async def download_slideshow_sync(url: str, attempt: int = 1):
    """Скачивание слайдшоу через gallery-dl (async функция)"""
    try:
        slideshow_id = os.urandom(4).hex()
        output_dir = TEMP_DIR / f"slideshow_{slideshow_id}"
        output_dir.mkdir(exist_ok=True)

        command = [
            "gallery-dl",
            "--directory", str(output_dir),
            "--filename", "{num:>02}.{extension}",
        ]

        # Добавляем cookies если файл существует (для обхода возрастного ограничения)
        if os.path.exists(COOKIES_PATH):
            cookies_size = os.path.getsize(COOKIES_PATH)
            logger.info(f"🍪 gallery-dl: Используем cookies из {COOKIES_PATH} ({cookies_size} байт)")
            command.extend(["--cookies", COOKIES_PATH])
        else:
            logger.warning(f"⚠️ gallery-dl: Cookies не найдены: {COOKIES_PATH}")

        command.append(url)

        logger.info(f"📷 Запуск gallery-dl для {url} (попытка {attempt}/{MAX_DOWNLOAD_RETRIES})")
        logger.debug(f"Команда: {' '.join(command)}")

        # Используем async subprocess
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
            stdout_text = stdout.decode('utf-8', errors='ignore')
            stderr_text = stderr.decode('utf-8', errors='ignore')
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            logger.error(f"⏱️ Таймаут gallery-dl (попытка {attempt}) - превышено 180 секунд")
            if attempt < MAX_DOWNLOAD_RETRIES:
                logger.info(f"🔄 Повторная попытка {attempt + 1}/{MAX_DOWNLOAD_RETRIES}")
                await asyncio.sleep(5)
                return await download_slideshow_sync(url, attempt + 1)
            return None

        # Логируем вывод gallery-dl для отладки
        if stdout_text:
            logger.debug(f"gallery-dl stdout: {stdout_text}")
        if stderr_text:
            logger.warning(f"gallery-dl stderr: {stderr_text}")

        # Проверяем код возврата
        if process.returncode != 0:
            logger.error(f"❌ gallery-dl вернул код ошибки {process.returncode}")

            # Проверяем на временные HTTP ошибки
            if is_retryable_error(stderr_text) and attempt < MAX_DOWNLOAD_RETRIES:
                logger.warning(f"⚠️ Временная ошибка сервера в gallery-dl (попытка {attempt})")
                wait_time = 5 if '429' in stderr_text else 3
                logger.info(f"🔄 Повторная попытка {attempt + 1}/{MAX_DOWNLOAD_RETRIES} через {wait_time}с")
                await asyncio.sleep(wait_time)
                return await download_slideshow_sync(url, attempt + 1)

            if "Unable to find" in stderr_text or "403" in stderr_text or "404" in stderr_text:
                logger.error("🚫 Контент недоступен или требует авторизации")
                return None

        # Возвращаем список путей к скачанным изображениям
        images = sorted(list(output_dir.glob("*.*")))
        if images:
            logger.info(f"✅ Скачано {len(images)} файлов: {[img.name for img in images]}")
            return [str(img) for img in images]
        else:
            logger.warning(f"⚠️ Не найдено файлов в {output_dir}")
            logger.warning(f"Содержимое папки: {list(output_dir.iterdir())}")
            return None
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания слайдшоу (попытка {attempt}): {str(e)}")
        if attempt < MAX_DOWNLOAD_RETRIES:
            logger.info(f"🔄 Повторная попытка {attempt + 1}/{MAX_DOWNLOAD_RETRIES}")
            await asyncio.sleep(3)
            return await download_slideshow_sync(url, attempt + 1)
        return None


def download_slideshow_with_ytdlp(url: str, attempt: int = 1):
    """Альтернативный метод скачивания слайдшоу через yt-dlp"""
    try:
        slideshow_id = os.urandom(4).hex()
        output_dir = TEMP_DIR / f"slideshow_ytdlp_{slideshow_id}"
        output_dir.mkdir(exist_ok=True)
        
        ydl_opts = {
            'format': 'best',
            'outtmpl': str(output_dir / '%(autonumber)s.%(ext)s'),
            'quiet': False,
            'no_warnings': False,
            'extract_flat': False,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            },
            'socket_timeout': 60
        }
        
        # Добавляем cookies если файл существует (для обхода возрастного ограничения)
        if os.path.exists(COOKIES_PATH):
            cookies_size = os.path.getsize(COOKIES_PATH)
            logger.info(f"🍪 Используем cookies из {COOKIES_PATH} ({cookies_size} байт)")
            ydl_opts['cookiefile'] = str(COOKIES_PATH)
        else:
            logger.warning(f"⚠️ Cookies не найдены: {COOKIES_PATH}")
        
        logger.info(f"🔄 Пробуем yt-dlp для слайдшоу: {url} (попытка {attempt}/{MAX_DOWNLOAD_RETRIES})")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
        # Собираем скачанные файлы
        files = sorted(list(output_dir.glob("*.*")))
        if files:
            logger.info(f"✅ yt-dlp скачал {len(files)} файлов")
            return [str(f) for f in files]
        return None
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if is_retryable_error(error_msg):
            logger.warning(f"⚠️ Временная ошибка сервера в yt-dlp (попытка {attempt}): {error_msg[:150]}")
            if attempt < MAX_DOWNLOAD_RETRIES:
                wait_time = 5 if 'HTTP Error 429' in error_msg else 3
                logger.info(f"🔄 Повторная попытка {attempt + 1}/{MAX_DOWNLOAD_RETRIES} через {wait_time}с")
                time.sleep(wait_time)
                return download_slideshow_with_ytdlp(url, attempt + 1)
        else:
            logger.error(f"❌ yt-dlp не смог скачать слайдшоу (попытка {attempt}): {error_msg[:200]}")
            if attempt < MAX_DOWNLOAD_RETRIES:
                logger.info(f"🔄 Повторная попытка {attempt + 1}/{MAX_DOWNLOAD_RETRIES}")
                time.sleep(3)
                return download_slideshow_with_ytdlp(url, attempt + 1)
        return None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Неожиданная ошибка в yt-dlp (попытка {attempt}): {error_msg[:200]}")
        if attempt < MAX_DOWNLOAD_RETRIES:
            logger.info(f"🔄 Повторная попытка {attempt + 1}/{MAX_DOWNLOAD_RETRIES}")
            time.sleep(3)
            return download_slideshow_with_ytdlp(url, attempt + 1)
        return None


def download_video_sync(url: str, attempt: int = 1):
    """Скачивание видео через yt-dlp (синхронная функция)"""
    try:
        video_id = os.urandom(4).hex()
        output_path = TEMP_DIR / f"tiktok_{video_id}"
        
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': str(output_path) + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            },
            'socket_timeout': 60
        }
        
        # Добавляем cookies если файл существует (для обхода возрастного ограничения)
        if os.path.exists(COOKIES_PATH):
            cookies_size = os.path.getsize(COOKIES_PATH)
            logger.info(f"🍪 Используем cookies из {COOKIES_PATH} ({cookies_size} байт)")
            ydl_opts['cookiefile'] = str(COOKIES_PATH)
        else:
            logger.warning(f"⚠️ Cookies не найдены: {COOKIES_PATH}")
        
        logger.info(f"🎥 Скачивание видео из {url} (попытка {attempt}/{MAX_DOWNLOAD_RETRIES})")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
            for ext in ['mp4', 'webm', 'mkv']:
                file_path = output_path.with_suffix(f'.{ext}')
                if file_path.exists():
                    return str(file_path)
            
            files = list(TEMP_DIR.glob(f"tiktok_{video_id}*"))
            return str(files[0]) if files else None
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if is_retryable_error(error_msg):
            logger.warning(f"⚠️ Временная ошибка сервера (попытка {attempt}): {error_msg[:150]}")
            if attempt < MAX_DOWNLOAD_RETRIES:
                wait_time = 5 if 'HTTP Error 429' in error_msg else 3
                logger.info(f"🔄 Повторная попытка {attempt + 1}/{MAX_DOWNLOAD_RETRIES} через {wait_time}с")
                time.sleep(wait_time)
                return download_video_sync(url, attempt + 1)
        else:
            logger.error(f"❌ Ошибка скачивания видео (попытка {attempt}): {error_msg[:200]}")
            if attempt < MAX_DOWNLOAD_RETRIES:
                logger.info(f"🔄 Повторная попытка {attempt + 1}/{MAX_DOWNLOAD_RETRIES}")
                time.sleep(3)
                return download_video_sync(url, attempt + 1)
        return None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Неожиданная ошибка (попытка {attempt}): {error_msg[:200]}")
        if attempt < MAX_DOWNLOAD_RETRIES:
            logger.info(f"🔄 Повторная попытка {attempt + 1}/{MAX_DOWNLOAD_RETRIES}")
            time.sleep(3)
            return download_video_sync(url, attempt + 1)
        return None


async def download_slideshow(url: str):
    """Скачивание слайдшоу с fallback на yt-dlp (асинхронная обёртка)"""
    # Сначала пробуем gallery-dl (теперь async)
    result = await download_slideshow_sync(url)

    # Если gallery-dl не смог, пробуем yt-dlp (синхронная функция)
    if result is None:
        logger.info("🔄 gallery-dl не смог скачать, пробуем yt-dlp...")
        result = await asyncio.get_event_loop().run_in_executor(None, download_slideshow_with_ytdlp, url)

    return result


async def download_video(url: str):
    """Скачивание видео (асинхронная обёртка)"""
    return await asyncio.get_event_loop().run_in_executor(None, download_video_sync, url)


async def download_tiktok_content(url: str):
    """Скачивание контента TikTok (автоопределение типа)"""
    if is_tiktok_slideshow(url):
        return await download_slideshow(url), "slideshow"
    else:
        return await download_video(url), "video"
