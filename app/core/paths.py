import os
import re

_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|]')
_MAX_NAME = 120

# 默认输出选项：与历史行为完全一致
_DEFAULT_MAX_NAME = 120


def _default_opts() -> dict:
    return {
        "organize_by_series": True,
        "filename_max_length": _DEFAULT_MAX_NAME,
        "illegal_char_replacement": "_",
        "illust_multipage_style": "folder",
        "title_max": 80,
        "author_max": 60,
        "series_max": 60,
    }


_DEFAULT_OPTS = _default_opts()


def output_opts(settings=None) -> dict:
    """从 Settings 生成输出选项；settings 为 None 时返回历史默认行为。"""
    if settings is None:
        return dict(_DEFAULT_OPTS)

    def _get(key, default):
        try:
            v = getattr(settings, key, default)
        except Exception:
            v = default
        return default if v is None else v

    repl = str(_get("illegal_char_replacement", "_"))
    if not repl or any(c in repl for c in '\\/:*?"<>|'):
        repl = "_"
    repl = repl[:3]

    try:
        maxlen = int(_get("filename_max_length", _DEFAULT_MAX_NAME))
    except (TypeError, ValueError):
        maxlen = _DEFAULT_MAX_NAME
    maxlen = max(40, min(200, maxlen))

    style = str(_get("illust_multipage_style", "folder"))
    if style not in ("folder", "flat"):
        style = "folder"

    ratio = maxlen / float(_DEFAULT_MAX_NAME)
    return {
        "organize_by_series": bool(_get("organize_by_series", True)),
        "filename_max_length": maxlen,
        "illegal_char_replacement": repl,
        "illust_multipage_style": style,
        "title_max": max(30, int(80 * ratio)),
        "author_max": max(20, int(60 * ratio)),
        "series_max": max(20, int(60 * ratio)),
    }


def sanitize_name(name: str, max_len: int = _MAX_NAME, replacement: str = "_") -> str:
    cleaned = _ILLEGAL_RE.sub(replacement, name or "").strip().rstrip(".")
    cleaned = cleaned[:max_len].strip()
    return cleaned or "untitled"


def author_dir_name(author_id: str, author_name: str, opts: dict = None) -> str:
    opts = opts or _DEFAULT_OPTS
    if author_name:
        nick = sanitize_name(author_name, opts["author_max"], opts["illegal_char_replacement"])
    else:
        nick = "unknown"
    return f"{author_id}_{nick}"


def author_md_name(author_id: str, author_name: str, opts: dict = None) -> str:
    return f"{author_dir_name(author_id, author_name, opts)}.md"


def author_illust_md_name(author_id: str, author_name: str, opts: dict = None) -> str:
    return f"{author_dir_name(author_id, author_name, opts)}_插画.md"


def illust_md_root(settings) -> str:
    root = (getattr(settings, "illust_md_root", "") or "").strip()
    return root or settings.md_root


def author_dir(epub_root: str, author_id: str, author_name: str, opts: dict = None) -> str:
    return os.path.join(epub_root, author_dir_name(author_id, author_name, opts))


def author_illust_md_path(settings, author_id: str, author_name: str, opts: dict = None) -> str:
    name = author_illust_md_name(author_id, author_name, opts)
    if settings.md_separate:
        return os.path.join(illust_md_root(settings), name)
    return os.path.join(author_dir(settings.epub_root, author_id, author_name, opts), name)


def novel_file_name(title: str, novel_id: str, pattern: str = "{title}.{id}",
                    author_name: str = "", opts: dict = None) -> str:
    opts = opts or _DEFAULT_OPTS
    rep = opts["illegal_char_replacement"]
    pattern = (pattern or "{title}.{id}").strip() or "{title}.{id}"
    stem = (
        pattern.replace("{title}", sanitize_name(title, opts["title_max"], rep))
        .replace("{id}", novel_id)
        .replace("{author}", sanitize_name(author_name, opts["author_max"], rep) if author_name else "")
    )
    stem = sanitize_name(stem, opts["filename_max_length"], rep)
    return f"{stem}.epub"


def novel_path(epub_root: str, novel: dict, pattern: str = "{title}.{id}", opts: dict = None) -> str:
    opts = opts or _DEFAULT_OPTS
    base = author_dir(epub_root, novel["author_id"], novel.get("author_name") or "", opts)
    if opts["organize_by_series"]:
        if novel.get("series_id"):
            series_dir = sanitize_name(
                novel.get("series_title") or novel["series_id"],
                opts["series_max"], opts["illegal_char_replacement"])
            base = os.path.join(base, "系列", series_dir)
        else:
            base = os.path.join(base, "单篇")
    name = novel_file_name(novel.get("title") or "untitled", novel["novel_id"], pattern,
                           novel.get("author_name") or "", opts)
    return os.path.join(base, name)


def resolve_collision(path: str, novel: dict, existing_path: str = "") -> str:
    if not os.path.exists(path):
        return path
    if existing_path and os.path.abspath(existing_path) == os.path.abspath(path):
        return path
    stem, ext = os.path.splitext(path)
    return f"{stem}.{novel['novel_id']}{ext}"


def author_md_path(settings, author_id: str, author_name: str, opts: dict = None) -> str:
    name = author_md_name(author_id, author_name, opts)
    if settings.md_separate:
        return os.path.join(settings.md_root, name)
    return os.path.join(author_dir(settings.epub_root, author_id, author_name, opts), name)


def migrate_md_files(settings, authors: list, logger=None) -> dict:
    report = {"moved": 0, "conflict": 0, "failed": 0}
    if not settings.epub_root:
        return report
    seen = set()
    for author in authors:
        aid = author["author_id"]
        if aid in seen:
            continue
        seen.add(aid)
        name = author.get("author_name") or ""
        for illust in (False, True):
            file_name = author_illust_md_name(aid, name) if illust else author_md_name(aid, name)
            unified = os.path.join(author_dir(settings.epub_root, aid, name), file_name)
            root = illust_md_root(settings) if illust else settings.md_root
            separate = os.path.join(root, file_name) if settings.md_separate else ""
            src, dst = (unified, separate) if settings.md_separate else (separate_legacy(settings, aid, illust), unified)
            if not src or not os.path.exists(src):
                continue
            if src == dst:
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.exists(dst):
                    with open(src, "r", encoding="utf-8") as f:
                        src_text = f.read()
                    with open(dst, "r", encoding="utf-8") as f:
                        dst_text = f.read()
                    if src_text == dst_text:
                        os.remove(src)
                        report["moved"] += 1
                    else:
                        report["conflict"] += 1
                    continue
                os.replace(src, dst)
                report["moved"] += 1
            except Exception:
                report["failed"] += 1
                if logger:
                    logger.warning("MD 迁移失败：%s -> %s", src, dst, exc_info=True)
    return report


def separate_legacy(settings, author_id: str, illust: bool = False) -> str:
    roots = []
    if illust:
        roots.append(illust_md_root(settings))
    roots.append(settings.md_root)
    prefix = f"{author_id}_"
    for root in dict.fromkeys(r for r in roots if r):
        if not os.path.isdir(root):
            continue
        for entry in os.listdir(root):
            if not (entry.startswith(prefix) and entry.endswith(".md")):
                continue
            is_illust = entry.endswith("_插画.md")
            if is_illust == illust:
                return os.path.join(root, entry)
    return ""


def illust_root(settings) -> str:
    root = (getattr(settings, "illust_root", "") or "").strip()
    if root:
        return root
    return os.path.join(settings.epub_root, "图片")


def illust_base_name(illust: dict, opts: dict = None) -> str:
    opts = opts or _DEFAULT_OPTS
    title = sanitize_name(illust.get("title") or "untitled", opts["title_max"],
                          opts["illegal_char_replacement"])
    return sanitize_name(f"{title}.{illust['illust_id']}", opts["filename_max_length"],
                         opts["illegal_char_replacement"])


def illust_path(root: str, illust: dict, ext: str = "", opts: dict = None) -> str:
    """folder 模式下的多页作品返回作品文件夹路径；其余情况返回图片文件路径。"""
    opts = opts or _DEFAULT_OPTS
    base = author_dir(root, illust["author_id"], illust.get("author_name") or "", opts)
    name = illust_base_name(illust, opts)
    multipage = int(illust.get("page_count") or 1) > 1
    if multipage and opts["illust_multipage_style"] == "folder":
        return os.path.join(base, name)
    return os.path.join(base, name + (ext or ".jpg"))


def illust_page_name(illust: dict, idx: int, ext: str, opts: dict = None) -> str:
    """flat（平铺）模式下的多页作品单页文件名。"""
    return f"{illust_base_name(illust, opts)}_p{idx}{ext}"


def illust_is_multipage_folder(illust: dict, opts: dict = None) -> bool:
    """当前配置下，该作品是否以「子文件夹」形式存放。"""
    opts = opts or _DEFAULT_OPTS
    if int(illust.get("page_count") or 1) <= 1:
        return False
    return opts["illust_multipage_style"] == "folder"
