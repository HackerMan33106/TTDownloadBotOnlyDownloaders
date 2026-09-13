"""
Загрузчик для PornHub
"""
import os
from typing import Optional, Dict, Any, Callable
from pathlib import Path
import yt_dlp

from .base import BaseDownloader, MAX_DOWNLOAD_SIZE, get_readonly_cookies_path
from config.settings import logger


class PornHubDownloader(BaseDownloader):
    """Загрузчик для PornHub"""
    
    def can_handle(self, url: str) -> bool:
        domains = [
            'pornhub.com', 'www.pornhub.com', 'rt.pornhub.com',
            'de.pornhub.com', 'fr.pornhub.com', 'es.pornhub.com',
            'it.pornhub.com', 'pt.pornhub.com', 'jp.pornhub.com',
        ]
        return any(domain in url.lower() for domain in domains)
    
    def get_supported_domains(self) -> list[str]:
        return ['pornhub.com']
    
    async def download(self, url: str, output_dir: str, progress_hook: Callable = None) -> Optional[Dict[str, Any]]:
        cookies_copy = None
        try:
            output_template = os.path.join(output_dir, '%(title)s.%(ext)s')
            opts = {
                'format': 'best[ext=mp4]/best',
                'outtmpl': output_template,
                'age_limit': 18,
                'nocheckcertificate': True,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                },
                'noplaylist': True,
                'max_filesize': MAX_DOWNLOAD_SIZE,
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 60,
            }

            # Добавляем cookies если файл существует
            cookies_copy = get_readonly_cookies_path()
            if cookies_copy:
                opts['cookiefile'] = cookies_copy

            if progress_hook:
                opts['progress_hooks'] = [progress_hook]
            
            logger.info(f"PornHub: Загрузка {url}")
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
            except Exception as e1:
                logger.warning(f"PornHub первая попытка: {e1}")
                opts['format'] = 'best'
                opts['force_generic_extractor'] = True
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
            logger.error(f"PornHub download error: {e}")
            return None
        finally:
            if cookies_copy and os.path.exists(cookies_copy):
                os.remove(cookies_copy)
