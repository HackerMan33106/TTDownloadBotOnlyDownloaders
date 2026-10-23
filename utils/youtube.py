"""Распознавание и нормализация ссылок YouTube Shorts."""
import re
from urllib.parse import urlsplit


_SHORTS_PATH = re.compile(r"/shorts/([A-Za-z0-9_-]{11})/?$")
_SHORTS_URL = re.compile(
    r"https?://(?:www\.|m\.)?youtube\.com/shorts/[A-Za-z0-9_-]{11}"
    r"/?(?:\?[^\s<>\"']*)?(?:\#[^\s<>\"']*)?"
    r"(?![A-Za-z0-9_/-])",
    re.IGNORECASE,
)


def clean_youtube_shorts_url(url: str) -> str | None:
    """Возвращает каноническую ссылку без трекинга, только для Shorts."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or parsed.netloc.lower() not in (
        "youtube.com", "www.youtube.com", "m.youtube.com",
    ):
        return None
    match = _SHORTS_PATH.fullmatch(parsed.path)
    if not match:
        return None
    return f"https://www.youtube.com/shorts/{match.group(1)}"


def extract_youtube_shorts_urls(text: str) -> list[str]:
    """Извлекает Shorts из текста, сохраняя порядок и убирая повторы."""
    urls = (clean_youtube_shorts_url(match.group(0)) for match in _SHORTS_URL.finditer(text or ""))
    return list(dict.fromkeys(url for url in urls if url))
