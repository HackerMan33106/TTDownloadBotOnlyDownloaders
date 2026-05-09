"""
Загрузчик для Bilibili
"""
import os
from typing import Optional, Dict, Any, Callable
from pathlib import Path
import yt_dlp

from .base import BaseDownloader, MAX_DOWNLOAD_SIZE
from config.settings import logger


class BilibiliDownloader(BaseDownloader):
    """Загрузчик для Bilibili"""
    
    def can_handle(self, url: str) -> bool:
        domains = ['bilibili.com', 'www.bilibili.com', 'bilibili.tv', 'www.bilibili.tv', 'b23.tv']
        return any(domain in url.lower() for domain in domains)
    
    def get_supported_domains(self) -> list[str]:
        return ['bilibili.com', 'bilibili.tv', 'b23.tv']
    
    async def download(self, url: str, output_dir: str, progress_hook: Callable = None) -> Optional[Dict[str, Any]]:
        try:
            output_template = os.path.join(output_dir, '%(title)s.%(ext)s')
            opts = {
                'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
                'merge_output_format': 'mp4',
                'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
                'outtmpl': output_template,
                'max_filesize': MAX_DOWNLOAD_SIZE,
                'quiet': True,
                'no_warnings': True,
            }
            if progress_hook:
                opts['progress_hooks'] = [progress_hook]
            
            logger.info(f"Bilibili: Загрузка {url}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            
            files = list(Path(output_dir).glob('*.mp4'))
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
            logger.error(f"Bilibili download error: {e}")
            return None
