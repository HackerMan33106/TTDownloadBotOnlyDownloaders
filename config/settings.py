"""
Конфигурация бота
"""
import os
import logging
import tempfile
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


# Фильтр для подавления сетевых ошибок
class NetworkErrorFilter(logging.Filter):
    """Фильтрует шумные сетевые ошибки из логов"""
    def filter(self, record):
        # Игнорируем сообщения о разрыве соединения
        if 'ServerDisconnectedError' in record.getMessage():
            return False
        if 'TelegramNetworkError' in record.getMessage():
            return False
        if 'Failed to fetch updates' in record.getMessage() and 'Server disconnected' in record.getMessage():
            return False
        return True


# Отключаем лишние логи
for lib in ['httpx', 'aiohttp', 'aiogram', 'yt-dlp']:
    lib_logger = logging.getLogger(lib)
    lib_logger.setLevel(logging.ERROR)
    lib_logger.addFilter(NetworkErrorFilter())

# Токен бота
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Spotify API credentials
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REFRESH_TOKEN = os.getenv('SPOTIFY_REFRESH_TOKEN')

# Администраторы (из переменных окружения)
PERMANENT_ADMIN_STR = os.getenv('PERMANENT_ADMIN', '')
PERMANENT_ADMIN = [int(x.strip()) for x in PERMANENT_ADMIN_STR.split(',') if x.strip().isdigit()]

MANUAL_USERS_IDS_STR = os.getenv('WHITELIST_USERS', '')
MANUAL_USERS_IDS = [int(x.strip()) for x in MANUAL_USERS_IDS_STR.split(',') if x.strip().isdigit()]

WHITELIST_USERS = PERMANENT_ADMIN + MANUAL_USERS_IDS

WHITELIST_GROUPS_STR = os.getenv('WHITELIST_GROUPS', '')
WHITELIST_GROUPS = [int(x.strip()) for x in WHITELIST_GROUPS_STR.split(',') if x.strip().lstrip('-').isdigit()]

# Trash group для кэширования медиафайлов (обязательная переменная окружения)
TRASH_GROUP_ID_STR = os.getenv("TRASH_GROUP_ID")
if TRASH_GROUP_ID_STR:
    try:
        TRASH_GROUP_ID = int(TRASH_GROUP_ID_STR)
    except ValueError:
        logger.error("❌ TRASH_GROUP_ID должен быть числом")
        TRASH_GROUP_ID = None
else:
    logger.warning("⚠️ TRASH_GROUP_ID не установлен - кэширование медиафайлов отключено")
    TRASH_GROUP_ID = None

# Режим отладки (переключается через /debug)
DEBUG_MODE = False

USE_LOCAL_API = os.getenv('USE_LOCAL_API', 'false').lower() == 'true'

# Автоопределение LOCAL_API_URL в зависимости от платформы
import platform
if USE_LOCAL_API:
    # Если переменная окружения задана явно - используем её
    if 'LOCAL_API_URL' in os.environ:
        LOCAL_API_URL = os.getenv('LOCAL_API_URL')
    else:
        # Автоопределение: Windows использует localhost:8200, Linux - Docker bridge:8200
        if platform.system() == 'Windows':
            LOCAL_API_URL = 'http://127.0.0.1:8200'
        else:
            # Linux в Docker - используем bridge IP
            LOCAL_API_URL = 'http://172.17.0.1:8200'
else:
    LOCAL_API_URL = os.getenv('LOCAL_API_URL', 'http://127.0.0.1:8081')

MAX_UPLOAD_SIZE_MB = 2000 if USE_LOCAL_API else 50

DATA_DIR = os.getenv('DATA_DIR', '.')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot_data.db")
COOKIES_PATH = os.path.join(DATA_DIR, "cookies.txt")

import subprocess

# Проверка ffmpeg
try:
    result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=15)
    if result.returncode == 0:
        version_line = result.stdout.split('\n')[0]
        logger.info(f"✅ ffmpeg найден: {version_line}")
    else:
        logger.warning("⚠️ ffmpeg не найден")
except Exception as e:
    logger.warning(f"⚠️ ffmpeg не найден: {e}")

# Проверка gallery-dl
try:
    result = subprocess.run(['gallery-dl', '--version'], capture_output=True, text=True, timeout=15)
    if result.returncode == 0:
        version = result.stdout.strip()
        logger.info(f"✅ gallery-dl найден: {version}")
    else:
        logger.warning("⚠️ gallery-dl не найден")
except Exception as e:
    logger.warning(f"⚠️ gallery-dl не найден: {e}")

# Проверка cookies
if os.path.exists(COOKIES_PATH):
    logger.info(f"🍪 Найден файл cookies: {COOKIES_PATH}")
else:
    logger.warning(f"⚠️ Файл cookies не найден: {COOKIES_PATH}")

TEMP_DIR = Path(tempfile.gettempdir()) / "tiktok_bot"
TEMP_DIR.mkdir(exist_ok=True)

# Настройка количества попыток скачивания
MAX_DOWNLOAD_RETRIES = 5
