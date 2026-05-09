"""
Функции для работы с TikTok URL
"""
import re
import requests
from config.settings import logger


# Паттерны TikTok ссылок
TIKTOK_PATTERNS = [
    r'https?://(?:www\.)?tiktok\.com/@[\w.-]*/video/\d+(?:\?[^\s]*)?',
    r'https?://(?:www\.)?tiktok\.com/@[\w.-]*/photo/\d+(?:\?[^\s]*)?',
    r'https?://(?:www\.)?tiktok\.com/t/[\w]+/?(?:\?[^\s]*)?',
    r'https?://v[mt]\.tiktok\.com/[\w]+/?(?:\?[^\s]*)?'
]


def is_tiktok_url(text: str) -> bool:
    """Проверяет, содержит ли текст TikTok ссылку"""
    if not text:
        return False
    return any(re.search(pattern, text) for pattern in TIKTOK_PATTERNS)


def extract_tiktok_url(text: str) -> str | None:
    """Извлекает первую TikTok ссылку из текста"""
    if not text:
        return None
    for pattern in TIKTOK_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def extract_all_tiktok_urls(text: str) -> list:
    """Извлекает все TikTok ссылки из текста"""
    if not text:
        return []
    urls = []
    for pattern in TIKTOK_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        urls.extend(matches)
    return list(set(urls))


def expand_short_url(url: str) -> str:
    """Разворачивает короткие TikTok ссылки"""
    try:
        # Проверяем, является ли ссылка короткой
        if 'tiktok.com/t/' in url or 'vm.tiktok.com' in url or 'vt.tiktok.com' in url:
            logger.info(f"🔗 Разворачиваю короткую ссылку: {url}")
            response = requests.head(url, allow_redirects=True, timeout=10)
            expanded = response.url
            return expanded
    except Exception as e:
        logger.warning(f"⚠️ Ошибка разворачивания: {str(e)}")
    return url


def clean_tiktok_url(url: str) -> str | None:
    """Очищает TikTok URL от лишних параметров и валидирует"""
    # Сначала разворачиваем короткую ссылку
    expanded_url = expand_short_url(url)
    
    # Проверяем на неподдерживаемые URL (только /explore в пути)
    if re.search(r'/explore(?:\?|$)', expanded_url):
        logger.warning(f"❌ Неподдерживаемая ссылка: {expanded_url}")
        return None
    
    # Оставляем только базовую часть URL
    # @[^/]* — допускаем пустой username (vt.tiktok.com может раскрываться в @/video/...)
    match = re.match(r'(https?://(?:www\.)?tiktok\.com/@[^/]*/(?:video|photo)/\d+)', expanded_url)
    if match:
        cleaned = match.group(1)
        logger.info(f"🧽 Очищенный URL: {cleaned}")
        return cleaned
    
    # Если после разворачивания URL всё ещё короткий (vm/vt.tiktok.com) —
    # значит redirect не сработал, но URL валидный — возвращаем как есть
    if re.match(r'https?://v[mt]\.tiktok\.com/[\w]+/?', expanded_url):
        logger.info(f"🔗 Короткая ссылка не развернулась, используем как есть: {expanded_url}")
        return expanded_url
    
    logger.warning(f"❌ Не удалось очистить URL: {expanded_url}")
    return None


def is_tiktok_slideshow(url: str) -> bool:
    """Проверяет, является ли URL слайдшоу"""
    return "/photo/" in url


def is_retryable_error(error_msg: str) -> bool:
    """Проверяет, является ли ошибка временной и требует повторной попытки"""
    retryable_patterns = [
        # HTTP ошибки
        'HTTP Error 503', 'HTTP Error 429', 'HTTP Error 500', 
        'HTTP Error 502', 'HTTP Error 504', 'HTTP Error 408',
        # Текстовые описания
        'Service Unavailable', 'Too Many Requests', 'Server Error',
        'Gateway Timeout', 'Request Timeout', 'Temporary failure',
        # Сетевые проблемы
        'Connection reset', 'Connection refused', 'Connection timed out',
        'timed out', 'timeout', 'URLError', 'HTTPError',
        # SSL/TLS проблемы
        'SSL', 'TLS', 'certificate',
        # Прочие сетевые ошибки
        'Network is unreachable', 'Host is unreachable',
        'No route to host', 'Connection aborted',
        # Коды ошибок
        '503', '429', '500', '502', '504', '408'
    ]
    return any(pattern.lower() in error_msg.lower() for pattern in retryable_patterns)
