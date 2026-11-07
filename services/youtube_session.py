"""Read browser-managed YouTube cookies and request one authenticated refresh."""
import json
import hashlib
import http.cookiejar
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from config.settings import COOKIES_PATH, DATA_DIR, TEMP_DIR, logger

_refresh_lock = threading.Lock()
_last_attempt = 0.0
_last_result = False


def _state_dir():
    return Path(os.getenv('YOUTUBE_SESSION_DIR', str(Path(DATA_DIR) / 'browser-session')))


def get_youtube_cookies_path():
    """Use an authenticated snapshot unless the user supplied newer cookies."""
    directory = _state_dir()
    try:
        status = json.loads((directory / 'status.json').read_text(encoding='utf-8'))
        if isinstance(status, dict) and status.get('authenticated') is True:
            source = directory / 'youtube.cookies.txt'
            manual = Path(COOKIES_PATH)
            if manual.is_file() and manual.stat().st_mtime_ns > source.stat().st_mtime_ns:
                from services.downloaders.base import get_readonly_cookies_path
                return get_readonly_cookies_path()
            descriptor, temporary = tempfile.mkstemp(prefix='youtube_cookies_', suffix='.txt', dir=TEMP_DIR)
            os.close(descriptor)
            try:
                shutil.copyfile(source, temporary)
                _remove_browser_visitor_cookies(Path(temporary))
                return temporary
            except OSError:
                os.unlink(temporary)
                raise
    except (OSError, ValueError, TypeError, KeyError):
        pass
    from services.downloaders.base import get_readonly_cookies_path
    return get_readonly_cookies_path()


def _remove_browser_visitor_cookies(path):
    """Let yt-dlp create its own visitor context while keeping account cookies.

    Browser visitor/session identifiers can make the extracted media URL return
    403 even though the account is signed in. Only the disposable copy changes.
    """
    visitor_names = {'YSC', 'VISITOR_INFO1_LIVE', 'VISITOR_PRIVACY_METADATA', '__Secure-ROLLOUT_TOKEN'}
    original = path.read_text(encoding='utf-8')
    lines = []
    for line in original.splitlines(keepends=True):
        fields = line.rstrip('\r\n').split('\t')
        if len(fields) == 7 and fields[5] in visitor_names:
            continue
        lines.append(line)
    cleaned = ''.join(lines)
    if cleaned != original:
        path.write_text(cleaned, encoding='utf-8')


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
                _stage_manual_cookies(directory)
                _last_result = _refresh_via_files(directory)
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


def _stage_manual_cookies(directory):
    """Pass manual YouTube cookies through the shared state, including atomic replacements."""
    source = Path(COOKIES_PATH)
    if not source.is_file():
        return
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    marker = directory / 'manual.source.json'
    target = directory / 'manual.cookies.txt'
    try:
        if json.loads(marker.read_text()).get('sha256') == fingerprint and target.is_file():
            return
    except (OSError, ValueError, AttributeError):
        pass
    jar = http.cookiejar.MozillaCookieJar(str(source))
    try:
        jar.load(ignore_discard=True)
    except (OSError, http.cookiejar.LoadError):
        logger.warning('YouTube: ручной файл cookies не удалось прочитать; сохранённая сессия оставлена')
        return
    youtube = http.cookiejar.MozillaCookieJar()
    for cookie in jar:
        domain = cookie.domain.lstrip('.').lower()
        if domain == 'youtube.com' or domain.endswith('.youtube.com'):
            youtube.set_cookie(cookie)
    if not len(youtube):
        return
    descriptor, temporary = tempfile.mkstemp(prefix='.manual-', dir=directory)
    os.close(descriptor)
    try:
        youtube.save(temporary, ignore_discard=True)
        os.replace(temporary, target)
        marker.write_text(json.dumps({'sha256': fingerprint}), encoding='ascii')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _refresh_via_files(directory, timeout=90):
    """Request refresh without depending on the bot container or its network."""
    requested_at = time.time()
    request_id = uuid.uuid4().hex
    descriptor, temporary = tempfile.mkstemp(prefix='.refresh-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(dict(id=request_id, requested_at=requested_at), output)
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
            if (isinstance(response, dict) and response.get('request_id') == request_id
                    and float(response.get('completed_at', 0)) >= requested_at):
                authenticated = response.get('authenticated') is True
                if not authenticated:
                    state = response.get('state')
                    if state == 'needs_login':
                        logger.warning('YouTube: браузер вышел из аккаунта; нужен ручной вход или новые cookies')
                    else:
                        logger.warning('YouTube: браузер не завершил обновление сессии (%s)',
                                       state if state in ('starting', 'temporary_error') else 'unknown')
                return authenticated
        except (OSError, ValueError, TypeError):
            pass
        time.sleep(0.25)
    logger.warning('YouTube: сервис обновления не ответил за %s секунд', timeout)
    return False


def is_youtube_auth_error(error):
    message = str(error).lower()
    return any(part in message for part in (
        'sign in to confirm', 'sign in to verify', 'cookies are no longer valid',
        'login required', 'requires authentication', 'use --cookies',
    ))
