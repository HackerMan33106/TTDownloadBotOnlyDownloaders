"""
Базовый класс для загрузчиков видео
"""
import os
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from urllib.parse import urlparse
import ipaddress
import socket

from config.settings import MAX_UPLOAD_SIZE_MB, COOKIES_PATH, TEMP_DIR, logger

# Максимальный размер файла для yt-dlp (2x от upload limit для запаса на сплиттинг)
MAX_DOWNLOAD_SIZE = int(MAX_UPLOAD_SIZE_MB * 2 * 1024 * 1024)


def download_images_via_gallery_dl(url: str, output_dir: str) -> Optional[Dict[str, Any]]:
    """
    Скачивает изображения через gallery-dl (фоллбек для фото-постов).
    Возвращает dict с file_path (или list путей) или None.
    """
    try:
        command = [
            "gallery-dl",
            "--directory", output_dir,
            "--filename", "{num:>02}.{extension}",
            "--range", "1-20",
        ]
        if os.path.exists(COOKIES_PATH):
            command.extend(["--cookies", COOKIES_PATH])
        command.append(url)

        logger.info(f"📷 gallery-dl fallback: {url}")
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)

        if result.returncode != 0 and result.stderr:
            logger.warning(f"gallery-dl stderr: {result.stderr[:300]}")

        image_exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
        files = sorted([
            str(f) for f in Path(output_dir).iterdir()
            if f.is_file() and f.suffix.lower() in image_exts
        ])

        if not files:
            # Любые файлы
            files = sorted([str(f) for f in Path(output_dir).iterdir() if f.is_file()])

        if not files:
            return None

        logger.info(f"✅ gallery-dl: скачано {len(files)} файлов")

        if len(files) == 1:
            return {
                'file_path': files[0],
                'title': Path(files[0]).stem,
                'thumbnail': None,
                'duration': None,
            }
        else:
            return {
                'file_path': files[0],
                'all_files': files,
                'title': Path(files[0]).stem,
                'thumbnail': None,
                'duration': None,
            }
    except subprocess.TimeoutExpired:
        logger.error("⏱️ gallery-dl timeout")
        return None
    except Exception as e:
        logger.error(f"❌ gallery-dl error: {e}")
        return None


def is_no_media_error(error_msg: str) -> bool:
    """Проверяет, является ли ошибка типа 'нет видео/медиа в посте'"""
    no_media_patterns = [
        "no video", "there is no video", "no video could be found",
        "unsupported url", "unable to download webpage",
    ]
    lower = error_msg.lower()
    return any(p in lower for p in no_media_patterns)


def is_safe_url(url: str) -> bool:
    """Проверяет URL на SSRF: запрещает internal/private IP адреса"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        # Блокируем localhost/private ranges
        try:
            ips = socket.getaddrinfo(hostname, None)
            for _, _, _, _, sockaddr in ips:
                ip = ipaddress.ip_address(sockaddr[0])
                if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                    return False
        except (socket.gaierror, ValueError):
            pass  # DNS resolution failed — let yt-dlp handle it
        return True
    except Exception:
        return False


class BaseDownloader(ABC):
    """Базовый класс для всех загрузчиков"""
    
    def __init__(self):
        self.name = self.__class__.__name__
    
    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Проверяет, может ли загрузчик обработать данный URL"""
        pass
    
    @abstractmethod
    async def download(self, url: str, output_dir: str, progress_hook: Callable = None) -> Optional[Dict[str, Any]]:
        """
        Скачивает видео по URL
        
        Args:
            url: URL для загрузки
            output_dir: Директория для сохранения
            progress_hook: Опциональная callback-функция для yt-dlp progress_hooks
        
        Returns:
            Dict с информацией о загруженном файле:
            {
                'file_path': str,  # Путь к файлу
                'title': str,      # Название
                'thumbnail': str,  # Путь к превью (опционально)
                'duration': int,   # Длительность в секундах (опционально)
            }
            Или None в случае ошибки
        """
        pass
    
    @abstractmethod
    def get_supported_domains(self) -> list[str]:
        """Возвращает список поддерживаемых доменов"""
        pass
