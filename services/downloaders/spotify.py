"""
Загрузчик для Spotify
Использует Spotify Web API для получения названия трека,
затем yt-dlp для поиска и загрузки с YouTube
"""
import os
import re
import asyncio
import base64
from typing import Optional, Dict, Any, Callable
from pathlib import Path
import yt_dlp

import requests

from .base import BaseDownloader
from config.settings import logger, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET


class SpotifyDownloader(BaseDownloader):
    """Загрузчик для Spotify через Spotify API + yt-dlp"""
    
    def __init__(self):
        super().__init__()
        self.client_id = SPOTIFY_CLIENT_ID
        self.client_secret = SPOTIFY_CLIENT_SECRET
        self._access_token = None
    
    def can_handle(self, url: str) -> bool:
        domains = ['spotify.com', 'open.spotify.com']
        return any(domain in url.lower() for domain in domains)
    
    def get_supported_domains(self) -> list[str]:
        return ['spotify.com', 'open.spotify.com']
    
    def _get_access_token(self) -> Optional[str]:
        """Получает access token через Client Credentials Flow"""
        if not self.client_id or not self.client_secret:
            return None
        try:
            auth_str = f"{self.client_id}:{self.client_secret}"
            auth_b64 = base64.b64encode(auth_str.encode()).decode()
            
            resp = requests.post(
                "https://accounts.spotify.com/api/token",
                headers={"Authorization": f"Basic {auth_b64}"},
                data={"grant_type": "client_credentials"},
                timeout=10
            )
            if resp.status_code == 200:
                self._access_token = resp.json()["access_token"]
                return self._access_token
        except Exception as e:
            logger.error(f"Spotify: Ошибка получения токена: {e}")
        return None
    
    def _extract_track_id(self, url: str) -> Optional[str]:
        """Извлекает ID трека из Spotify URL"""
        # https://open.spotify.com/track/3fzdjCGmv8HPUNeljvXPhL?si=...
        match = re.search(r'track/([a-zA-Z0-9]+)', url)
        return match.group(1) if match else None
    
    def _get_track_info(self, track_id: str) -> Optional[Dict]:
        """Получает информацию о треке через Spotify API"""
        token = self._access_token or self._get_access_token()
        if not token:
            return None
        
        try:
            resp = requests.get(
                f"https://api.spotify.com/v1/tracks/{track_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                artists = ", ".join([a["name"] for a in data.get("artists", [])])
                return {
                    "name": data.get("name", "Unknown"),
                    "artists": artists,
                    "search_query": f"{artists} - {data.get('name', '')}"
                }
        except Exception as e:
            logger.error(f"Spotify: Ошибка получения информации: {e}")
        return None
    
    async def download(self, url: str, output_dir: str, progress_hook: Callable = None) -> Optional[Dict[str, Any]]:
        try:
            # Определяем поисковый запрос
            track_id = self._extract_track_id(url)
            search_query = None
            track_title = None
            
            if track_id:
                info = self._get_track_info(track_id)
                if info:
                    search_query = info["search_query"]
                    track_title = f"{info['artists']} - {info['name']}"
                    logger.info(f"Spotify: Найден трек: {track_title}")
            
            if not search_query:
                search_query = url
                logger.info(f"Spotify: Не удалось получить инфо, пробуем URL напрямую")
            
            output_template = os.path.join(output_dir, '%(title)s.%(ext)s')
            
            if search_query != url:
                yt_query = f"ytsearch1:{search_query}"
            else:
                yt_query = url
            
            opts = {
                'format': 'bestaudio',
                'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}],
                'noplaylist': True,
                'outtmpl': output_template,
                'quiet': True,
                'no_warnings': True,
            }
            if progress_hook:
                opts['progress_hooks'] = [progress_hook]
            
            logger.info(f"Spotify: Загрузка через yt-dlp: {yt_query}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([yt_query])
            
            # Ищем загруженный файл
            audio_extensions = ['*.mp3', '*.m4a', '*.opus', '*.ogg', '*.webm', '*.wav', '*.flac', '*.aac']
            files = []
            for ext in audio_extensions:
                files.extend(Path(output_dir).glob(ext))
            
            if not files:
                logger.error("Spotify: Аудио файл не найден после загрузки")
                return None
            
            file_path = str(files[0])
            title = track_title or Path(file_path).stem
            
            return {
                'file_path': file_path,
                'title': title,
                'thumbnail': None,
                'duration': None
            }
            
        except Exception as e:
            logger.error(f"Spotify download error: {e}")
            return None
