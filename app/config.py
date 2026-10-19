import copy
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
    theme: str = "light"  # light / dark / auto

    # ------------------------------------------------------------------
    # 高级设置：默认值全部等价于历史行为，改动才会改变抓取 / 输出方式
    # ------------------------------------------------------------------
    # --- 一、触发与筛选 ---
    # 作者账号 404 时：disable=自动停用(原行为) / keep=保持启用仅记录 / skip=本轮跳过不改状态
    author_404_action: str = "disable"
    disabled_recheck_days: int = 0        # 停用作者自动复查间隔(天)，0=不复查(原行为)
    max_novels_per_author: int = 0        # 每作者单次最多新增处理数，0=不限(原行为)
    tag_whitelist: list = field(default_factory=list)  # 标签白名单，空=不限制(原行为)
    keyword_include: str = ""             # 标题/简介需包含的词，空=不限制(原行为)
    keyword_exclude: str = ""             # 标题/简介命中则跳过，空=不限制(原行为)
    min_text_length: int = 0              # 正文最短字数，0=不限(原行为)
    catch_up_window_minutes: int = 0      # 错过定时点的补跑窗口(分钟)，0=当天任意时间都补跑(原行为)

    # --- 二、文件输出与命名 ---
    organize_by_series: bool = True       # 按「单篇 / 系列」分子目录，关=全部平铺(原行为 True)
    illust_multipage_style: str = "folder"  # 多页插画：folder=子文件夹(原行为) / flat=平铺
    write_md_index: bool = True           # 生成 MD 索引(原行为 True)
    md_include_description: bool = True   # MD 条目含作品简介(原行为 True)
    md_include_stats: bool = True         # MD 头部含本轮抓取统计(原行为 True)
    md_single_sort: str = "publish"       # 单篇排序：publish=发布时间(原行为) / updated / id
    filename_max_length: int = 120        # 文件名最大长度(原行为 120)
    illegal_char_replacement: str = "_"   # 非法字符替换(原行为 "_")

    # --- 三、文件内容编排 ---
    epub_include_info_page: bool = True   # 生成「作品信息页」(原行为 True)
    epub_include_cover: bool = True       # 嵌入封面(原行为 True)
    epub_cover_fallback: bool = True      # 无封面时用第一张插图(原行为 True)
    epub_include_description: bool = True # 信息页含简介(原行为 True)
    epub_include_tags_meta: bool = True   # 标签写入书籍元数据(原行为 True)
    epub_include_source_url: bool = True  # 信息页含原文链接(原行为 True)
    epub_download_inserts: bool = True    # 下载正文内嵌插图(原行为 True)
    chapter_title_style: str = "marker"   # 章节标题：marker=有标记用标记否则第N页(原行为) / page / first_line
    epub_language: str = "ja"             # 书籍语言标记(原行为 "ja")

    # --- 四、更新与去重 ---
    update_mode: str = "timestamp"        # 更新判定：timestamp=比较远端更新时间(原行为) / always / never
    redownload_missing: bool = True       # 本地文件缺失时补下载(原行为 True)
    retry_failed_each_run: bool = True    # 每轮开头优先重试失败队列(原行为 True)
    replace_old_file: bool = True         # 新文件生成后删除旧文件(原行为 True)
    series_recheck_days: int = 30         # 已删除系列复查间隔(天)(原行为 30)
    remote_deleted_action: str = "keep"   # 远端删除：keep=保留文件并标记(原行为) / silent=保留不标记
    author_rename_migrate: bool = True    # 作者改昵称时自动迁移目录(原行为 True)

    # --- 五、抓取前自动备份 ---
    auto_backup_enabled: bool = True      # 每轮抓取前自动备份索引与设置(含 Cookie)
    auto_backup_keep: int = 5             # 自动备份保留份数

    # --- 六、小说排行榜（独立任务，不跟随每日抓取）---
    ranking_enabled: bool = False         # 是否启用排行榜抓取
    ranking_modes: list = field(default_factory=lambda: ["daily", "weekly"])
    ranking_include_r18: bool = False     # 是否抓取 R-18 榜单（需要登录 Cookie）
    ranking_download: bool = True         # 是否顺便下载正文（复用单篇管线）
    ranking_download_mode: str = "new"    # new=仅首次上榜下载 / follow_update=跟随更新判定
    ranking_language_filter: bool = True  # 只保留简体中文作品（R-18 榜同样处理）
    ranking_csv_enabled: bool = True      # 每次抓取后在下方目录自动生成一份 CSV
    ranking_csv_dir: str = ""             # CSV 存放目录；留空则用数据目录下的 rank_csv
    ranking_txt_log: bool = True          # 每次抓取后往 logs 目录追加一行人话摘要（可直接双击看）

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


# ---------------------------------------------------------------------------
# 高级设置清单：用于「一键恢复默认」。
# 只包含高级设置，恢复默认不会动存储路径、Cookie、代理、主题等基础配置。
# ---------------------------------------------------------------------------
ADVANCED_SETTING_KEYS = [
    # 一、触发与筛选
    "author_404_action", "disabled_recheck_days", "max_novels_per_author",
    "tag_whitelist", "keyword_include", "keyword_exclude", "min_text_length",
    "catch_up_window_minutes",
    # 二、文件输出与命名
    "organize_by_series", "illust_multipage_style", "write_md_index",
    "md_include_description", "md_include_stats", "md_single_sort",
    "filename_max_length", "illegal_char_replacement",
    # 三、文件内容编排
    "epub_include_info_page", "epub_include_cover", "epub_cover_fallback",
    "epub_include_description", "epub_include_tags_meta", "epub_include_source_url",
    "epub_download_inserts", "chapter_title_style", "epub_language",
    # 四、更新与去重
    "update_mode", "redownload_missing", "retry_failed_each_run",
    "replace_old_file", "series_recheck_days", "remote_deleted_action",
    "author_rename_migrate",
    # 五、抓取前自动备份
    "auto_backup_enabled", "auto_backup_keep",
]


def reset_advanced_settings(settings: Settings) -> None:
    """把高级设置恢复为默认值（等价于历史行为），其余配置保持不变。"""
    defaults = Settings()
    for key in ADVANCED_SETTING_KEYS:
        setattr(settings, key, copy.deepcopy(getattr(defaults, key)))
