import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field

APP_NAME = "PixivNovelScraper"


def _legacy_appdata_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def app_data_dir() -> str:
    override = os.environ.get("PIXIV_DATA_DIR")
    if override:
        path = override
    elif getattr(sys, "frozen", False):
        path = os.path.join(os.path.dirname(sys.executable), "data")
    else:
        path = _legacy_appdata_dir()
    legacy = _legacy_appdata_dir()
    if (os.path.abspath(path) != os.path.abspath(legacy)
            and os.path.exists(legacy) and not os.path.exists(path)):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.move(legacy, path)
        except OSError:
            pass
    os.makedirs(path, exist_ok=True)
    return path


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class ProxyConfig:
    enabled: bool = False
    scheme: str = "http"
    host: str = ""
    port: int = 1080
    username: str = ""
    password: str = ""


@dataclass
class Settings:
    epub_root: str = ""
    md_root: str = ""
    fetch_time: str = "03:00"
    request_interval: float = 1.5
    max_consecutive_failures: int = 10
    disk_threshold_mb: int = 500
    r18_enabled: bool = False
    tag_blacklist: list = field(default_factory=list)
    user_agent: str = DEFAULT_UA
    name_pattern: str = "{title}.{id}"
    autostart: bool = False
    risk_accepted: bool = False
    onboarded: bool = False
    illust_root: str = ""
    illust_md_root: str = ""
    proxy: ProxyConfig = field(default_factory=ProxyConfig)

    @property
    def md_separate(self) -> bool:
        return bool(self.md_root.strip())


def config_path() -> str:
    return os.path.join(app_data_dir(), "config.json")


def load_settings() -> Settings:
    path = config_path()
    if not os.path.exists(path):
        return Settings()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        proxy_raw = raw.pop("proxy", {}) or {}
        settings = Settings(**{k: v for k, v in raw.items() if k in Settings.__dataclass_fields__ and k != "proxy"})
        settings.proxy = ProxyConfig(**{k: v for k, v in proxy_raw.items() if k in ProxyConfig.__dataclass_fields__})
        return settings
    except Exception:
        return Settings()


def save_settings(settings: Settings) -> None:
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(asdict(settings), f, ensure_ascii=False, indent=2)
