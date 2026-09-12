import json
import time
from dataclasses import asdict

from .. import credentials
from ..config import ProxyConfig, Settings, save_settings

BACKUP_VERSION = 1


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
            }
            for a in authors
        ],
        "cookie": cookie or "",
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


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
        if db.add_author(aid, a.get("author_name") or ""):
            added += 1
        db.set_author_enabled(aid, bool(a.get("enabled", 1)))

    cookie = (payload.get("cookie") or "").strip()
    if cookie:
        credentials.save_cookie(cookie)

    return {
        "settings": settings,
        "authors_total": len(authors),
        "authors_added": added,
        "cookie_set": bool(cookie),
    }
