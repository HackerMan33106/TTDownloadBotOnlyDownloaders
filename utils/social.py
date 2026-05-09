"""
Функции для работы с URL социальных сетей (X/Twitter, Reddit, Facebook)
"""
import re
from config.settings import logger


# Паттерны ссылок для социальных сетей
SOCIAL_PATTERNS = {
    "twitter": [
        r'https?://(?:www\.)?(?:twitter|x)\.com/\w+/status/\d+(?:\?[^\s]*)?',
        r'https?://(?:www\.)?(?:fixupx|fxtwitter|vxtwitter)\.com/\w+/status/\d+(?:\?[^\s]*)?',
    ],
    "reddit": [
        r'https?://(?:www\.)?reddit\.com/r/\w+/comments/\w+(?:/[^\s?]*)?(?:\?[^\s]*)?',
        r'https?://(?:www\.)?redd\.it/\w+',
        r'https?://(?:i\.)?redd\.it/\w+\.\w+',
    ],
    "facebook": [
        r'https?://(?:www\.)?facebook\.com/.+/(?:posts|videos|photo|photos)/[\w.]+(?:\?[^\s]*)?',
        r'https?://(?:www\.)?facebook\.com/photo(?:\?[^\s]*)?',
        r'https?://(?:www\.)?facebook\.com/(?:watch|reel)/?\?v=\d+(?:&[^\s]*)?',
        r'https?://(?:www\.)?facebook\.com/share/(?:v/|p/|r/)?\w+(?:\?[^\s]*)?',
        r'https?://fb\.watch/\w+/?(?:\?[^\s]*)?',
    ],
}

# Все паттерны в одном списке
ALL_SOCIAL_PATTERNS = []
for patterns in SOCIAL_PATTERNS.values():
    ALL_SOCIAL_PATTERNS.extend(patterns)


def get_platform_name(url: str) -> str | None:
    """Определяет платформу по URL"""
    if not url:
        return None
    for platform, patterns in SOCIAL_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return platform
    return None


def get_platform_display_name(platform: str) -> str:
    """Возвращает красивое название платформы"""
    names = {
        "twitter": "𝕏 (Twitter)",
        "reddit": "Reddit",
        "facebook": "Facebook",
    }
    return names.get(platform, platform)


def get_platform_emoji(platform: str) -> str:
    """Возвращает эмодзи платформы"""
    emojis = {
        "twitter": "🐦",
        "reddit": "🟠",
        "facebook": "🔵",
    }
    return emojis.get(platform, "🌐")


def is_social_url(text: str) -> bool:
    """Проверяет, содержит ли текст ссылку на поддерживаемую соцсеть"""
    if not text:
        return False
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in ALL_SOCIAL_PATTERNS)


def extract_social_url(text: str) -> str | None:
    """Извлекает первую ссылку на соцсеть из текста"""
    if not text:
        return None
    for pattern in ALL_SOCIAL_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def extract_all_social_urls(text: str) -> list:
    """Извлекает все ссылки на соцсети из текста"""
    if not text:
        return []
    urls = []
    for pattern in ALL_SOCIAL_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        urls.extend(matches)
    return list(set(urls))


def clean_social_url(url: str) -> str:
    """Очищает URL от лишних параметров трекинга"""
    # Для twitter/x - убираем fix-прокси
    url = re.sub(r'(https?://)(?:www\.)?(?:fixupx|fxtwitter|vxtwitter)\.com/',
                 r'\1x.com/', url)
    
    # Убираем tracking параметры (но НЕ fbid, set — они нужны для Facebook)
    url = re.sub(r'[?&](?:utm_\w+|ref|fbclid|igshid|si)=[^\s&]*', '', url)
    # Убираем оставшийся ? или & в конце
    url = re.sub(r'[?&]$', '', url)
    
    return url
