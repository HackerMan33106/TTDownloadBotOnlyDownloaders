"""
Утилиты для отображения прогресса загрузки видео.
Прогресс-бар с троттлингом для Telegram (обновление раз в 3 секунды).
"""
import math
import time
from typing import Optional
from config.settings import logger


def create_progress_bar(percent: float, length: int = 20) -> str:
    """Генерирует строку прогресс-бара ██████░░░░"""
    percent = max(0.0, min(100.0, percent))
    filled = int(math.floor(percent / 100 * length))
    empty = length - filled
    return "█" * filled + "░" * empty


class DownloadProgress:
    """
    Состояние загрузки для одного URL.
    Используется как shared state между потоком yt-dlp и async updater loop.
    Thread-safe по дизайну: yt-dlp пишет в поля, а updater только читает.
    """

    def __init__(self, url: str, platform: str):
        self.url = url
        self.platform = platform
        self.is_finished = False
        self.error: Optional[str] = None

        # Данные прогресса (обновляются из progress_hook в потоке yt-dlp)
        self.downloaded_bytes: int = 0
        self.total_bytes: int = 0
        self.percent: float = 0.0
        self.speed: float = 0.0       # bytes/sec
        self.eta: Optional[int] = None # секунды

        # Фаза: "connecting" -> "downloading" -> "processing" -> "finished"
        self.phase: str = "connecting"

    def make_progress_hook(self):
        """Возвращает функцию-хук для yt-dlp progress_hooks"""
        state = self

        def hook(d: dict):
            status = d.get('status', '')

            if status == 'downloading':
                state.phase = "downloading"
                state.downloaded_bytes = d.get('downloaded_bytes', 0)
                state.total_bytes = (
                    d.get('total_bytes')
                    or d.get('total_bytes_estimate')
                    or 0
                )
                if state.total_bytes > 0:
                    state.percent = (state.downloaded_bytes / state.total_bytes) * 100
                state.speed = d.get('speed') or 0
                state.eta = d.get('eta')

            elif status == 'finished':
                state.phase = "processing"  # постобработка (мерж и т.д.)
                state.percent = 100.0

            elif status == 'error':
                state.error = d.get('error', 'Unknown error')
                state.is_finished = True

        return hook

    def format_status_text(self, url_info: str = "") -> str:
        """Формирует текст статуса для сообщения в Telegram"""
        if self.phase == "connecting":
            return (
                f"⏳ {url_info}Подключение...\n\n"
                f"Платформа: {self.platform}\n"
                f"URL: {self.url}"
            )

        if self.phase == "processing":
            return (
                f"⚙️ {url_info}Обработка файла...\n\n"
                f"Платформа: {self.platform}\n"
                f"URL: {self.url}"
            )

        # downloading
        bar = create_progress_bar(self.percent)
        dl_mb = self.downloaded_bytes / (1024 * 1024)

        if self.total_bytes > 0:
            tot_mb = self.total_bytes / (1024 * 1024)
            size_text = f"{dl_mb:.1f} МБ / {tot_mb:.1f} МБ"
        else:
            size_text = f"{dl_mb:.1f} МБ"

        speed_text = ""
        if self.speed and self.speed > 0:
            speed_mb = self.speed / (1024 * 1024)
            if speed_mb >= 1:
                speed_text = f"\n⚡ {speed_mb:.1f} МБ/с"
            else:
                speed_kb = self.speed / 1024
                speed_text = f"\n⚡ {speed_kb:.0f} КБ/с"

        eta_text = ""
        if self.eta and self.eta > 0:
            mins, secs = divmod(int(self.eta), 60)
            if mins > 0:
                eta_text = f" • ~{mins}м {secs}с"
            else:
                eta_text = f" • ~{secs}с"

        return (
            f"⏳ {url_info}Загрузка...\n"
            f"{bar} {self.percent:.0f}%\n"
            f"{size_text}{speed_text}{eta_text}\n\n"
            f"Платформа: {self.platform}\n"
            f"URL: {self.url}"
        )
