import base64
import json
import os
import time
from dataclasses import asdict

from .. import credentials
from ..config import ProxyConfig, Settings, app_data_dir, save_settings

BACKUP_VERSION = 3


def _encrypt_cookie(cookie: str) -> dict:
    """把 Cookie 用 DPAPI 加密后放进备份；无 DPAPI 环境退回明文并标注。

    备份文件可能被用户拷走 / 上传，明文 Cookie 躺在里面是实打实的泄露风险。
    """
    if not cookie:
        return {"cookie": "", "cookie_encrypted": False}
    if credentials.dpapi_available():
        import win32crypt
        blob = win32crypt.CryptProtectData(cookie.encode("utf-8"), "pixiv-backup",
                                           None, None, None, 0)
        return {"cookie": base64.b64encode(blob).decode("ascii"),
                "cookie_encrypted": True}
    return {"cookie": cookie, "cookie_encrypted": False}


def _decrypt_cookie(cookie: str, encrypted: bool) -> str:
    if not cookie:
        return ""
    if encrypted:
        import win32crypt
        try:
            blob = base64.b64decode(cookie.encode("ascii"))
            _, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
            return data.decode("utf-8")
        except Exception:
            return ""
    return cookie


def export_backup(path: str, settings: Settings, authors: list, cookie: str) -> None:
    payload = {
        "app": "PixivNovelScraper",
        "version": BACKUP_VERSION,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "settings": asdict(settings),
        "authors": [
            {
                "author_id": a["author_id"],
                "author_name": a.get("author_name") or "",
                "enabled": int(a.get("enabled", 1)),
                "kind": a.get("kind") or "novel",
            }
            for a in authors
        ],
        **_encrypt_cookie(cookie or ""),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def auto_backup_dir() -> str:
    """自动备份存放目录（data/backups），与 data 同属被 gitignore 的运行时数据。"""
    return os.path.join(app_data_dir(), "backups")


def create_auto_backup(settings: Settings, db, cookie: str, keep: int = 5,
                       logger=None) -> str:
    """抓取前的滚动自动备份，返回本次备份文件路径；失败返回空串。

    注意：备份**明文包含 Cookie**（与手动导出一致），存放在本地 data/backups 下，
    请勿外传或上传。超出 keep 份数的旧备份会被删除。
    """
    directory = auto_backup_dir()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"backup_{time.strftime('%Y%m%d_%H%M%S')}.json")
    export_backup(path, settings, db.list_authors(), cookie or "")
    try:
        keep_n = max(1, min(50, int(keep or 1)))
    except (TypeError, ValueError):
        keep_n = 5
    files = sorted(f for f in os.listdir(directory)
                   if f.startswith("backup_") and f.endswith(".json"))
    for name in files[:-keep_n]:
        try:
            os.remove(os.path.join(directory, name))
        except OSError:
            pass
    if logger:
        logger.info("抓取前自动备份：%s（保留最近 %d 份，含 Cookie，请勿外传）", path, keep_n)
    return path


def import_backup(path: str, db) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict) or payload.get("app") != "PixivNovelScraper":
        raise ValueError("不是本程序的备份文件")

    raw_settings = payload.get("settings") or {}
    proxy_raw = raw_settings.pop("proxy", {}) or {}
    settings = Settings(**{k: v for k, v in raw_settings.items()
                           if k in Settings.__dataclass_fields__ and k != "proxy"})
    settings.proxy = ProxyConfig(**{k: v for k, v in proxy_raw.items()
                                    if k in ProxyConfig.__dataclass_fields__})
    save_settings(settings)

    authors = payload.get("authors") or []
    added = 0
    for a in authors:
        aid = str(a.get("author_id") or "").strip()
        if not aid.isdigit():
            continue
        kind = a.get("kind") if a.get("kind") in ("novel", "illust") else "novel"
        if db.add_author(aid, a.get("author_name") or "", kind=kind):
            added += 1
        db.set_author_enabled(aid, bool(a.get("enabled", 1)), kind=kind)

    # 兼容旧版（v2 及更早）明文 Cookie，以及新版 DPAPI 加密 Cookie
    raw_cookie = (payload.get("cookie") or "").strip()
    encrypted = bool(payload.get("cookie_encrypted", False))
    cookie = _decrypt_cookie(raw_cookie, encrypted)
    if cookie:
        credentials.save_cookie(cookie)

    return {
        "settings": settings,
        "authors_total": len(authors),
        "authors_added": added,
        "cookie_set": bool(cookie),
    }
