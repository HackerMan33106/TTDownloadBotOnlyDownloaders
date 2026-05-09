"""
Сервис скачивания контента из социальных сетей (X/Twitter, Reddit, Facebook)
Использует gallery-dl и yt-dlp как backend
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
    MAX_UPLOAD_SIZE_MB,
    logger
)
from utils.social import get_platform_name, get_platform_display_name


def _is_no_media_error(error_msg: str) -> bool:
    """Проверяет, является ли ошибка 'нет видео/медиа' (не стоит ретраить)"""
    patterns = [
        "no video", "there is no video", "no video could be found",
        "no media found", "unsupported url", "is not a video",
    ]
    lower = error_msg.lower()
    return any(p in lower for p in patterns)


def download_social_images_sync(url: str, attempt: int = 1) -> list | None:
    """Скачивает изображения через gallery-dl (синхронная)"""
    try:
        download_id = os.urandom(4).hex()
        output_dir = TEMP_DIR / f"social_{download_id}"
        output_dir.mkdir(exist_ok=True)

        command = [
            "gallery-dl",
            "--directory", str(output_dir),
            "--filename", "{num:>02}.{extension}",
            "--range", "1-20",  # Лимит фото
        ]

        if os.path.exists(COOKIES_PATH):
            command.extend(["--cookies", COOKIES_PATH])

        command.append(url)

        platform = get_platform_name(url) or "unknown"
        logger.info(f"📷 Запуск gallery-dl для {platform}: {url} (попытка {attempt}/{MAX_DOWNLOAD_RETRIES})")

        result = subprocess.run(command, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            if result.stderr:
                logger.warning(f"gallery-dl stderr: {result.stderr[:300]}")
            if attempt < MAX_DOWNLOAD_RETRIES:
                time.sleep(3)
                return download_social_images_sync(url, attempt + 1)
            return None

        # Собираем скачанные файлы
        image_extensions = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
        files = sorted([
            str(f) for f in output_dir.iterdir()
            if f.suffix.lower() in image_extensions
        ])

        if files:
            logger.info(f"✅ gallery-dl: скачано {len(files)} изображений")
            return files

        logger.warning("⚠️ gallery-dl: изображения не найдены")
        return None

    except subprocess.TimeoutExpired:
        logger.error(f"⏱️ Таймаут gallery-dl (попытка {attempt})")
        if attempt < MAX_DOWNLOAD_RETRIES:
            return download_social_images_sync(url, attempt + 1)
        return None
    except Exception as e:
        logger.error(f"❌ gallery-dl ошибка: {str(e)[:200]}")
        if attempt < MAX_DOWNLOAD_RETRIES:
            return download_social_images_sync(url, attempt + 1)
        return None


def download_social_video_sync(url: str, attempt: int = 1) -> str | None:
    """Скачивает видео через yt-dlp (синхронная)"""
    try:
        video_id = os.urandom(4).hex()
        output_path = TEMP_DIR / f"social_{video_id}"

        ydl_opts = {
            'outtmpl': str(output_path) + '.%(ext)s',
            'format': f'best[filesize<{MAX_UPLOAD_SIZE_MB}M]/best',
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'retries': 3,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            },
        }

        if os.path.exists(COOKIES_PATH):
            ydl_opts['cookiefile'] = COOKIES_PATH

        platform = get_platform_name(url) or "unknown"
        logger.info(f"🎬 Запуск yt-dlp для {platform}: {url} (попытка {attempt}/{MAX_DOWNLOAD_RETRIES})")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

            for ext in ['mp4', 'webm', 'mkv']:
                file_path = output_path.with_suffix(f'.{ext}')
                if file_path.exists():
                    size_mb = os.path.getsize(str(file_path)) / (1024 * 1024)
                    logger.info(f"✅ yt-dlp: скачано видео ({size_mb:.1f}MB)")
                    return str(file_path)

            # Пробуем найти файл с любым расширением
            files = list(TEMP_DIR.glob(f"social_{video_id}*"))
            if files:
                return str(files[0])

        return None

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        # Не ретраим если это "нет видео" — сразу fallback на gallery-dl
        if _is_no_media_error(error_msg):
            logger.info(f"ℹ️ yt-dlp: нет видео в посте, переходим к gallery-dl")
            return None
        logger.warning(f"⚠️ yt-dlp ошибка (попытка {attempt}): {error_msg[:200]}")
        if attempt < MAX_DOWNLOAD_RETRIES:
            time.sleep(3)
            return download_social_video_sync(url, attempt + 1)
        return None
    except Exception as e:
        logger.error(f"❌ yt-dlp ошибка: {str(e)[:200]}")
        if attempt < MAX_DOWNLOAD_RETRIES:
            time.sleep(3)
            return download_social_video_sync(url, attempt + 1)
        return None


async def download_social_content(url: str) -> tuple:
    """
    Скачивает контент из соцсети (автоопределение типа)
    
    Returns:
        tuple: (content_path, content_type)
        content_type: "images" (list путей), "video" (str путь), None
    """
    loop = asyncio.get_event_loop()
    
    # Сначала пробуем скачать изображения (через gallery-dl) — быстрее для фото-постов
    images = await loop.run_in_executor(None, download_social_images_sync, url)
    if images:
        return images, "images"
    
    # Если изображений нет, пробуем скачать как видео (через yt-dlp)
    video_path = await loop.run_in_executor(None, download_social_video_sync, url)
    if video_path:
        return video_path, "video"
    
    return None, None
