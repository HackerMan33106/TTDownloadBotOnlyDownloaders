"""
Загрузчик для VK (ВКонтакте)
"""
import os
from typing import Optional, Dict, Any, Callable
from pathlib import Path
import yt_dlp

from .base import BaseDownloader, MAX_DOWNLOAD_SIZE, download_images_via_gallery_dl
from config.settings import logger


class VKDownloader(BaseDownloader):
    """Загрузчик для VK/VK Music/VK Video"""
    
    def can_handle(self, url: str) -> bool:
        domains = ['vk.com', 'vk.ru', 'm.vk.com', 'vkvideo.ru']
        return any(domain in url.lower() for domain in domains)
    
    def get_supported_domains(self) -> list[str]:
        return ['vk.com', 'vk.ru', 'vkvideo.ru']
    
    async def download(self, url: str, output_dir: str, progress_hook: Callable = None) -> Optional[Dict[str, Any]]:
        try:
            output_template = os.path.join(output_dir, '%(title)s.%(ext)s')
            opts = {
                'format': 'best',
                'outtmpl': output_template,
                'max_filesize': MAX_DOWNLOAD_SIZE,
                'quiet': True,
                'no_warnings': True,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                },
            }
            
            # Подключаем cookies для VK (авторизация/капча)
            cookies_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'cookies.txt')
            if os.path.exists(cookies_path):
                opts['cookiefile'] = cookies_path
            
            if progress_hook:
                opts['progress_hooks'] = [progress_hook]
            
            logger.info(f"VK: Загрузка {url}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            
            files = list(Path(output_dir).glob('*'))
            if not files:
                return None
            file_path = str(files[0])
            return {
                'file_path': file_path,
                'title': Path(file_path).stem,
                'thumbnail': None,
                'duration': None
            }
        except Exception as e:
            logger.error(f"VK download error: {e}")
            # Фоллбек на gallery-dl для фото
            logger.info("VK: пробуем gallery-dl для фото...")
            return download_images_via_gallery_dl(url, output_dir)
