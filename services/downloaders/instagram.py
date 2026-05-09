"""
Загрузчик для Instagram (Reels, Posts, Stories)
"""
import os
import requests
from typing import Optional, Dict, Any, Callable
from pathlib import Path
import yt_dlp

from .base import BaseDownloader, MAX_DOWNLOAD_SIZE, download_images_via_gallery_dl
from config.settings import logger


class InstagramDownloader(BaseDownloader):
    """Загрузчик для Instagram"""
    
    def can_handle(self, url: str) -> bool:
        domains = ['instagram.com', 'www.instagram.com', 'instagr.am']
        return any(domain in url.lower() for domain in domains)
    
    def get_supported_domains(self) -> list[str]:
        return ['instagram.com']
    
    def _download_image_via_ytdlp_info(self, url: str, output_dir: str) -> Optional[Dict[str, Any]]:
        """Извлекает URL изображения через yt-dlp extract_info и скачивает напрямую"""
        try:
            cookies_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'cookies.txt')
            opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                },
            }
            if os.path.exists(cookies_path):
                opts['cookiefile'] = cookies_path
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            
            if not info:
                return None
            
            # Ищем URL изображения в thumbnails
            image_url = None
            if info.get('thumbnails'):
                thumbs = sorted(info['thumbnails'], key=lambda t: t.get('width', 0) * t.get('height', 0), reverse=True)
                image_url = thumbs[0].get('url')
            if not image_url and info.get('thumbnail'):
                image_url = info['thumbnail']
            
            if not image_url:
                return None
            
            title = info.get('title', 'instagram_photo') or 'instagram_photo'
            safe_title = "".join(c for c in title if c.isalnum() or c in " -_")[:50] or 'instagram_photo'
            file_path = os.path.join(output_dir, f"{safe_title}.jpg")
            
            logger.info(f"Instagram: скачиваем изображение напрямую...")
            resp = requests.get(image_url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }, timeout=30)
            
            if resp.status_code == 200 and len(resp.content) > 1000:
                with open(file_path, 'wb') as f:
                    f.write(resp.content)
                logger.info(f"✅ Instagram: фото скачано ({len(resp.content) / 1024:.0f}KB)")
                return {
                    'file_path': file_path,
                    'title': safe_title,
                    'thumbnail': None,
                    'duration': None
                }
            return None
        except Exception as e:
            logger.warning(f"Instagram extract_info fallback failed: {e}")
            return None
    
    async def download(self, url: str, output_dir: str, progress_hook: Callable = None) -> Optional[Dict[str, Any]]:
        try:
            output_template = os.path.join(output_dir, '%(title)s.%(ext)s')
            opts = {
                'format': 'best',
                'outtmpl': output_template,
                'noplaylist': True,
                'max_filesize': MAX_DOWNLOAD_SIZE,
                'quiet': True,
                'no_warnings': True,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                },
            }
            
            cookies_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'cookies.txt')
            if os.path.exists(cookies_path):
                opts['cookiefile'] = cookies_path
            
            if progress_hook:
                opts['progress_hooks'] = [progress_hook]
            
            logger.info(f"Instagram: Загрузка {url}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            
            media_exts = ['*.mp4', '*.jpg', '*.jpeg', '*.png', '*.webp', '*.webm']
            files = []
            for ext in media_exts:
                files.extend(Path(output_dir).glob(ext))
            
            if not files:
                files = [f for f in Path(output_dir).iterdir() if f.is_file()]
            
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
            error_msg = str(e).lower()
            logger.error(f"Instagram download error: {e}")
            
            # Если "нет видео" — пробуем extract_info для получения фото
            if "no video" in error_msg or "there is no video" in error_msg:
                logger.info("Instagram: пробуем extract_info для фото...")
                result = self._download_image_via_ytdlp_info(url, output_dir)
                if result:
                    return result
            
            # Фоллбек на gallery-dl
            logger.info("Instagram: пробуем gallery-dl для фото...")
            return download_images_via_gallery_dl(url, output_dir)
