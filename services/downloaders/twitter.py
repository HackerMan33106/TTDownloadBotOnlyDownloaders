"""
Загрузчик для X (Twitter)
"""
import os
from typing import Optional, Dict, Any, Callable
from pathlib import Path
import yt_dlp

from .base import BaseDownloader, MAX_DOWNLOAD_SIZE, download_images_via_gallery_dl
from config.settings import logger


class TwitterDownloader(BaseDownloader):
    """Загрузчик для X (Twitter)"""
    
    def can_handle(self, url: str) -> bool:
        domains = ['twitter.com', 'x.com', 'www.twitter.com', 'mobile.twitter.com', 'www.x.com']
        return any(domain in url.lower() for domain in domains)
    
    def get_supported_domains(self) -> list[str]:
        return ['twitter.com', 'x.com']
    
    async def download(self, url: str, output_dir: str, progress_hook: Callable = None) -> Optional[Dict[str, Any]]:
        try:
            output_template = os.path.join(output_dir, '%(title)s.%(ext)s')
            opts = {
                'outtmpl': output_template,
                'max_filesize': MAX_DOWNLOAD_SIZE,
                'quiet': True,
                'no_warnings': True,
            }
            if progress_hook:
                opts['progress_hooks'] = [progress_hook]
            
            logger.info(f"X/Twitter: Загрузка {url}")
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
            logger.error(f"X/Twitter download error: {e}")
            # Фоллбек на gallery-dl для фото-твитов
            logger.info("X/Twitter: пробуем gallery-dl для фото...")
            return download_images_via_gallery_dl(url, output_dir)
