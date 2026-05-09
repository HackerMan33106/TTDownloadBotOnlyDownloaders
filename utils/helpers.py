"""
Вспомогательные функции
"""
import os
import random
import shutil
import subprocess
import unicodedata
from pathlib import Path
from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest

from utils.messages import DENY_MESSAGES
from config.settings import TEMP_DIR, logger
from database.audio import load_audio_url_storage, delete_audio_url_storage


# Последнее использованное сообщение (для избежания повторов)
_last_deny_message = None


def transliterate_name(name: str) -> str:
    """Транслитерация имени (арабский, кириллица и другие -> латиница)"""
    if not name:
        return name
    
    # Словарь для арабских букв (базовая транслитерация)
    arabic_to_latin = {
        'ا': 'a', 'أ': 'a', 'إ': 'i', 'آ': 'aa',
        'ب': 'b', 'ت': 't', 'ث': 'th',
        'ج': 'j', 'ح': 'h', 'خ': 'kh',
        'د': 'd', 'ذ': 'dh',
        'ر': 'r', 'ز': 'z',
        'س': 's', 'ش': 'sh',
        'ص': 's', 'ض': 'd',
        'ط': 't', 'ظ': 'z',
        'ع': 'a', 'غ': 'gh',
        'ف': 'f', 'ق': 'q',
        'ك': 'k', 'ل': 'l',
        'م': 'm', 'ن': 'n',
        'ه': 'h', 'ة': 'h',
        'و': 'w', 'ي': 'y', 'ى': 'a',
        'ء': "'",
        # Русские буквы
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd',
        'е': 'e', 'ё': 'yo', 'ж': 'zh', 'з': 'z', 'и': 'i',
        'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
        'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't',
        'у': 'u', 'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch',
        'ш': 'sh', 'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '',
        'э': 'e', 'ю': 'yu', 'я': 'ya',
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D',
        'Е': 'E', 'Ё': 'Yo', 'Ж': 'Zh', 'З': 'Z', 'И': 'I',
        'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M', 'Н': 'N',
        'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T',
        'У': 'U', 'Ф': 'F', 'Х': 'Kh', 'Ц': 'Ts', 'Ч': 'Ch',
        'Ш': 'Sh', 'Щ': 'Shch', 'Ъ': '', 'Ы': 'Y', 'Ь': '',
        'Э': 'E', 'Ю': 'Yu', 'Я': 'Ya'
    }
    
    result = []
    for char in name:
        if char in arabic_to_latin:
            result.append(arabic_to_latin[char])
        elif char.isascii():
            result.append(char)
        else:
            # Для остальных символов используем Unicode decomposition
            try:
                normalized = unicodedata.normalize('NFKD', char)
                ascii_char = ''.join(c for c in normalized if c.isascii())
                if ascii_char:
                    result.append(ascii_char)
                else:
                    result.append('?')
            except:
                result.append('?')
    
    transliterated = ''.join(result)
    # Убираем лишние пробелы и символы
    transliterated = ' '.join(transliterated.split())
    
    return transliterated if transliterated else 'User'


def format_user_name(name: str) -> str:
    """Форматирует имя пользователя с транслитерацией если нужно"""
    if not name or name == "Unknown" or name == "Не указано":
        return name
    return transliterate_name(name) if not name.isascii() else name


def get_random_deny_message() -> str:
    """Возвращает случайное сообщение об отказе, избегая повторов"""
    global _last_deny_message
    
    if len(DENY_MESSAGES) == 1:
        return DENY_MESSAGES[0]
    
    # Выбираем случайное сообщение, отличное от предыдущего
    available_messages = [msg for msg in DENY_MESSAGES if msg != _last_deny_message]
    message = random.choice(available_messages)
    _last_deny_message = message
    return message


def get_user_link(user: types.User) -> str:
    """Создает информативную строку о пользователе для логов"""
    name = user.first_name or "Unknown"
    
    # Транслитерируем имя если содержит не-ASCII символы
    if not name.isascii():
        name = transliterate_name(name)
    
    if user.username:
        return f"{user.id}({name}) (@{user.username})"
    else:
        return f"{user.id}({name}) (tg://user?id={user.id})"


async def get_username_by_id(user_id: int, bot: Bot) -> str:
    """Получает username или ссылку на пользователя по ID"""
    try:
        chat = await bot.get_chat(chat_id=user_id)
        
        if chat.username:
            return f"@{chat.username}"
        else:
            first_name = chat.first_name or "Пользователь"
            return f'<a href="tg://user?id={user_id}">{first_name}</a>'
    
    except TelegramBadRequest:
        return f"ID: {user_id}"


def create_delete_button(message: "types.Message" = None, user_id: int = None, message_id: int = None,
                        button_text: str = "🗑️ Удалить", callback_prefix: str = "delete_universal"):
    """
    Создает универсальную inline-кнопку для удаления сообщений

    Args:
        message: Объект сообщения команды (если передан, автоматически извлекаются user_id и message_id)
        user_id: ID пользователя, который может удалить сообщение (опционально, если передан message)
        message_id: ID сообщения команды для удаления (опционально, если передан message)
        button_text: Текст кнопки (по умолчанию "🗑️ Удалить")
        callback_prefix: Префикс для callback_data (по умолчанию "delete_universal")

    Returns:
        InlineKeyboardMarkup с кнопкой удаления

    Examples:
        # Способ 1: Передать объект сообщения (рекомендуется)
        create_delete_button(message)

        # Способ 2: Явно указать параметры (для обратной совместимости)
        create_delete_button(user_id=123, message_id=456, callback_prefix="delete_msg")
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    # Если передан объект message, извлекаем из него данные
    if message is not None:
        user_id = message.from_user.id
        message_id = message.message_id

    # Формируем callback_data
    if message_id is None:
        callback_data = f"{callback_prefix}:{user_id}"
    else:
        callback_data = f"{callback_prefix}:{user_id}:{message_id}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=button_text,
            callback_data=callback_data
        )]
    ])


def create_media_caption(user: "types.User", url: str = None, media_type: str = "video",
                        title: str = None, audio_from_button: bool = False) -> str:
    """
    Создает универсальную подпись для медиафайлов (видео/аудио)

    Args:
        user: Объект пользователя Telegram
        url: URL источника (опционально)
        media_type: Тип медиа - "video" или "audio" (по умолчанию "video")
        title: Название видео/трека (опционально, для видео)
        audio_from_button: True если аудио запрошено через кнопку на видео (по умолчанию False)

    Returns:
        Отформатированная строка подписи

    Examples:
        # Видео с URL
        create_media_caption(user, url="https://tiktok.com/...", media_type="video", title="Название")
        # Результат: "🎬 Название\n@username\n\nhttps://tiktok.com/..."

        # Аудио через атрибут -a с URL
        create_media_caption(user, url="https://youtube.com/...", media_type="audio")
        # Результат: "@username\nhttps://youtube.com/..."

        # Аудио через кнопку (без URL)
        create_media_caption(user, media_type="audio", audio_from_button=True, title="Название трека")
        # Результат: "🎵 Название трека\n@username"
    """
    # Формируем отображаемое имя пользователя
    if user.username:
        username_display = f"@{user.username}"
    else:
        username_display = user.first_name or "User"

    # Формируем подпись в зависимости от типа медиа
    if media_type == "video":
        # Паттерн для видео: "🎬 Название\n@username\n\nURL"
        parts = []
        if title:
            parts.append(f"🎬 {title}")
        parts.append(username_display)
        if url:
            parts.append(f"\n{url}")
        return "\n".join(parts)

    elif media_type == "audio":
        if audio_from_button:
            # Паттерн для аудио через кнопку: "🎵 Название\n@username"
            parts = []
            if title:
                parts.append(f"🎵 {title}")
            else:
                parts.append("🎵 Аудио")
            parts.append(username_display)
            return "\n".join(parts)
        else:
            # Паттерн для аудио через атрибут -a: "@username\nURL"
            parts = [username_display]
            if url:
                parts.append(url)
            return "\n".join(parts)

    # Fallback
    return username_display


async def cleanup_old_audio_files():
    """Очищает из audio_url_storage записи с несуществующими audio_path"""
    storage = await load_audio_url_storage()
    cleaned_count = 0

    for audio_id, data in list(storage.items()):
        if isinstance(data, dict) and "audio_path" in data:
            audio_path = data["audio_path"]
            if audio_path and not os.path.exists(audio_path):
                # Файл не существует, удаляем из хранилища
                await delete_audio_url_storage(audio_id)
                cleaned_count += 1

    if cleaned_count > 0:
        logger.info(f"🧹 Очищено {cleaned_count} устаревших записей из БД")


def get_disk_usage():
    """Возвращает информацию об использовании диска"""
    try:
        total, used, free = shutil.disk_usage("/")
        return {
            "total_gb": total / (1024**3),
            "used_gb": used / (1024**3),
            "free_gb": free / (1024**3),
            "used_percent": (used / total) * 100
        }
    except Exception:
        return None


def get_temp_dir_size():
    """Возвращает размер папки с временными файлами"""
    try:
        total_size = 0
        file_count = 0
        for item in TEMP_DIR.rglob('*'):
            if item.is_file():
                total_size += item.stat().st_size
                file_count += 1
        return total_size, file_count
    except Exception:
        return 0, 0


def check_ffmpeg() -> bool:
    """Проверяет наличие ffmpeg в системе"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            version_line = result.stdout.split('\n')[0]
            logger.info(f"✅ ffmpeg найден: {version_line}")
            return True
        else:
            logger.error("❌ ffmpeg не работает корректно")
            return False
    except FileNotFoundError:
        logger.error("❌ ffmpeg НЕ УСТАНОВЛЕН!")
        logger.error("📦 Установите: sudo apt update && sudo apt install ffmpeg -y")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка проверки ffmpeg: {e}")
        return False


def check_gallery_dl() -> bool:
    """Проверяет наличие gallery-dl в системе"""
    try:
        result = subprocess.run(
            ["gallery-dl", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            logger.info(f"✅ gallery-dl найден: {version}")
            return True
        else:
            logger.error("❌ gallery-dl не работает корректно")
            return False
    except FileNotFoundError:
        logger.error("❌ gallery-dl НЕ УСТАНОВЛЕН!")
        logger.error("📦 Установите: pip install gallery-dl")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка проверки gallery-dl: {e}")
        return False
