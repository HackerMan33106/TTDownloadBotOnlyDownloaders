"""
Функции очистки и проверки системы
"""
import os
import time
import shutil
import subprocess

from config.settings import TEMP_DIR, logger
from database.audio import load_audio_url_storage, delete_audio_url_storage


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


def cleanup_old_temp_files():
    """Очищает старые временные файлы (старше 1 часа)"""
    try:
        if not TEMP_DIR.exists():
            return
        
        current_time = time.time()
        one_hour_ago = current_time - 3600  # 1 час
        cleaned_count = 0
        freed_space = 0
        
        # Удаляем старые файлы
        for item in TEMP_DIR.rglob('*'):
            if item.is_file():
                try:
                    file_age = item.stat().st_mtime
                    if file_age < one_hour_ago:
                        file_size = item.stat().st_size
                        item.unlink()
                        cleaned_count += 1
                        freed_space += file_size
                except Exception:
                    pass
        
        # Удаляем пустые папки
        for item in TEMP_DIR.rglob('*'):
            if item.is_dir() and not any(item.iterdir()):
                try:
                    item.rmdir()
                except Exception:
                    pass
        
        if cleaned_count > 0:
            freed_mb = freed_space / (1024 * 1024)
            logger.info(f"🧹 Очищено {cleaned_count} временных файлов ({freed_mb:.1f} MB)")
    except Exception:
        pass


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
