"""
Загрузчик для Dzen.ru (Яндекс Дзен)
"""
import os
from typing import Optional, Dict, Any, Callable
from pathlib import Path
import yt_dlp

from .base import BaseDownloader, MAX_DOWNLOAD_SIZE
from config.settings import logger


class DzenDownloader(BaseDownloader):
    """Загрузчик для Dzen.ru"""
    
    def can_handle(self, url: str) -> bool:
        domains = ['dzen.ru', 'www.dzen.ru', 'zen.yandex.ru', 'www.zen.yandex.ru']
        return any(domain in url.lower() for domain in domains)
    
    def get_supported_domains(self) -> list[str]:
        return ['dzen.ru']
    
    async def download(self, url: str, output_dir: str, progress_hook: Callable = None) -> Optional[Dict[str, Any]]:
        try:
            output_template = os.path.join(output_dir, '%(title)s.%(ext)s')
            opts = {
                'format': 'best[ext=mp4]/best',
                'outtmpl': output_template,
                'noplaylist': True,
                'extractor_args': {'generic': {'impersonate': True}},
                'max_filesize': MAX_DOWNLOAD_SIZE,
                'quiet': True,
                'no_warnings': True,
            }
            if progress_hook:
                opts['progress_hooks'] = [progress_hook]
            
            logger.info(f"Dzen: Загрузка {url}")
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
            logger.error(f"Dzen download error: {e}")
            return None
