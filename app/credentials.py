import base64
import os

from .config import app_data_dir

try:
    import win32crypt
    _HAS_DPAPI = True
except ImportError:
    _HAS_DPAPI = False

_COOKIE_FILE = os.path.join(app_data_dir(), "credential.bin")


def save_cookie(cookie: str) -> None:
    data = cookie.encode("utf-8")
    if _HAS_DPAPI:
        blob = win32crypt.CryptProtectData(data, "pixiv-cookie", None, None, None, 0)
        payload = base64.b64encode(blob)
    else:
        payload = base64.b64encode(data)
    with open(_COOKIE_FILE, "wb") as f:
        f.write(payload)


def load_cookie() -> str:
    if not os.path.exists(_COOKIE_FILE):
        return ""
    try:
        with open(_COOKIE_FILE, "rb") as f:
            payload = base64.b64decode(f.read())
        if _HAS_DPAPI:
            _, data = win32crypt.CryptUnprotectData(payload, None, None, None, 0)
            return data.decode("utf-8")
        return payload.decode("utf-8")
    except Exception:
        return ""


def clear_cookie() -> None:
    if os.path.exists(_COOKIE_FILE):
        os.remove(_COOKIE_FILE)


def dpapi_available() -> bool:
    return _HAS_DPAPI
