"""
Загрузчик для YouTube и YouTube Music
"""
import os
from typing import Optional, Dict, Any, Callable
from pathlib import Path
import yt_dlp

from .base import BaseDownloader, MAX_DOWNLOAD_SIZE
from config.settings import logger


class YouTubeDownloader(BaseDownloader):
    """Загрузчик для YouTube/YouTube Music"""
    
    def can_handle(self, url: str) -> bool:
        domains = ['youtube.com', 'youtu.be', 'music.youtube.com', 'www.youtube.com', 'm.youtube.com']
        return any(domain in url.lower() for domain in domains)
    
    def get_supported_domains(self) -> list[str]:
        return ['youtube.com', 'youtu.be', 'music.youtube.com']
    
    async def download(self, url: str, output_dir: str, progress_hook: Callable = None) -> Optional[Dict[str, Any]]:
        try:
            output_template = os.path.join(output_dir, '%(title)s.%(ext)s')
            
            opts = {
                'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best',
                'merge_output_format': 'mp4',
                'outtmpl': output_template,
                'noplaylist': True,
                'max_filesize': MAX_DOWNLOAD_SIZE,
                'quiet': True,
                'no_warnings': True,
            }
            if progress_hook:
                opts['progress_hooks'] = [progress_hook]
            
            logger.info(f"YouTube: Загрузка {url}")
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
            logger.error(f"YouTube download error: {e}")
            return None
