"""Read browser-managed YouTube cookies and request one authenticated refresh."""
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from config.settings import DATA_DIR, TEMP_DIR, logger

_refresh_lock = threading.Lock()
_last_attempt = 0.0
_last_result = False


def _state_dir():
    return Path(os.getenv('YOUTUBE_SESSION_DIR', str(Path(DATA_DIR) / 'browser-session')))


def get_youtube_cookies_path():
    """Reuse the last authenticated snapshot until YouTube requests a refresh."""
    directory = _state_dir()
    try:
        status = json.loads((directory / 'status.json').read_text(encoding='utf-8'))
        if isinstance(status, dict) and status.get('authenticated') is True:
            source = directory / 'youtube.cookies.txt'
            descriptor, temporary = tempfile.mkstemp(prefix='youtube_cookies_', suffix='.txt', dir=TEMP_DIR)
            os.close(descriptor)
            try:
                shutil.copyfile(source, temporary)
                return temporary
            except OSError:
                os.unlink(temporary)
                raise
    except (OSError, ValueError, TypeError, KeyError):
        pass
    from services.downloaders.base import get_readonly_cookies_path
    return get_readonly_cookies_path()


def refresh_youtube_session():
    """Ask the browser to refresh without exposing the session API publicly."""
    global _last_attempt, _last_result
    directory = _state_dir()
    token_path = directory / 'api.token'
    file_transport = (directory / 'transport.json').is_file()
    if not file_transport and not token_path.is_file():
        return False
    with _refresh_lock:
        if time.monotonic() - _last_attempt < 20:
            return _last_result
        _last_attempt = time.monotonic()
        _last_result = False
        try:
            if file_transport:
                _last_result = _refresh_via_files(directory)
                if not _last_result:
                    logger.warning('YouTube: браузерная сессия требует входа или временно недоступна')
                return _last_result
            token = token_path.read_text(encoding='ascii').strip()
            base = os.getenv('YOUTUBE_SESSION_URL', 'http://cookie-browser:8089').rstrip('/')
            request = Request(base + '/refresh', data=b'{}', method='POST', headers={
                'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
            })
            with urlopen(request, timeout=45) as response:
                result = json.load(response)
            _last_result = isinstance(result, dict) and result.get('authenticated') is True
            if not _last_result:
                logger.warning('YouTube: браузерная сессия требует входа или временно недоступна')
        except (OSError, ValueError, TypeError):
            logger.warning('YouTube: сервис обновления браузерной сессии недоступен')
        return _last_result


def _refresh_via_files(directory, timeout=45):
    """Request refresh without depending on the bot container or its network."""
    requested_at = time.time()
    descriptor, temporary = tempfile.mkstemp(prefix='.refresh-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(dict(id=uuid.uuid4().hex, requested_at=requested_at), output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, directory / 'refresh.request.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = json.loads((directory / 'refresh.response.json').read_text(encoding='utf-8'))
            if isinstance(response, dict) and float(response.get('completed_at', 0)) >= requested_at:
                return response.get('authenticated') is True
        except (OSError, ValueError, TypeError):
            pass
        time.sleep(0.25)
    return False


def is_youtube_auth_error(error):
    message = str(error).lower()
    return any(part in message for part in (
        'sign in to confirm', 'sign in to verify', 'cookies are no longer valid',
        'login required', 'requires authentication', 'use --cookies',
    ))
