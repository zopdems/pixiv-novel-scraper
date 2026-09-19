import os
import re

_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|]')
_MAX_NAME = 120


def sanitize_name(name: str, max_len: int = _MAX_NAME) -> str:
    cleaned = _ILLEGAL_RE.sub("_", name or "").strip().rstrip(".")
    cleaned = cleaned[:max_len].strip()
    return cleaned or "untitled"


def author_dir_name(author_id: str, author_name: str) -> str:
    nick = sanitize_name(author_name, 60) if author_name else "unknown"
    return f"{author_id}_{nick}"


def author_md_name(author_id: str, author_name: str) -> str:
    return f"{author_dir_name(author_id, author_name)}.md"


def author_illust_md_name(author_id: str, author_name: str) -> str:
    return f"{author_dir_name(author_id, author_name)}_插画.md"


def illust_md_root(settings) -> str:
    root = (getattr(settings, "illust_md_root", "") or "").strip()
    return root or settings.md_root


def author_illust_md_path(settings, author_id: str, author_name: str) -> str:
    name = author_illust_md_name(author_id, author_name)
    if settings.md_separate:
        return os.path.join(illust_md_root(settings), name)
    return os.path.join(author_dir(settings.epub_root, author_id, author_name), name)


def novel_file_name(title: str, novel_id: str, pattern: str = "{title}.{id}", author_name: str = "") -> str:
    pattern = (pattern or "{title}.{id}").strip() or "{title}.{id}"
    stem = (
        pattern.replace("{title}", sanitize_name(title, 80))
        .replace("{id}", novel_id)
        .replace("{author}", sanitize_name(author_name, 40) if author_name else "")
    )
    stem = sanitize_name(stem, _MAX_NAME)
    return f"{stem}.epub"


def author_dir(epub_root: str, author_id: str, author_name: str) -> str:
    return os.path.join(epub_root, author_dir_name(author_id, author_name))


def novel_path(epub_root: str, novel: dict, pattern: str = "{title}.{id}") -> str:
    base = author_dir(epub_root, novel["author_id"], novel.get("author_name") or "")
    if novel.get("series_id"):
        series_dir = sanitize_name(novel.get("series_title") or novel["series_id"], 60)
        base = os.path.join(base, "系列", series_dir)
    else:
        base = os.path.join(base, "单篇")
    name = novel_file_name(novel.get("title") or "untitled", novel["novel_id"], pattern,
                           novel.get("author_name") or "")
    return os.path.join(base, name)


def resolve_collision(path: str, novel: dict, existing_path: str = "") -> str:
    if not os.path.exists(path):
        return path
    if existing_path and os.path.abspath(existing_path) == os.path.abspath(path):
        return path
    stem, ext = os.path.splitext(path)
    return f"{stem}.{novel['novel_id']}{ext}"


def author_md_path(settings, author_id: str, author_name: str) -> str:
    name = author_md_name(author_id, author_name)
    if settings.md_separate:
        return os.path.join(settings.md_root, name)
    return os.path.join(author_dir(settings.epub_root, author_id, author_name), name)


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


def illust_base_name(illust: dict) -> str:
    title = sanitize_name(illust.get("title") or "untitled", 80)
    return sanitize_name(f"{title}.{illust['illust_id']}", _MAX_NAME)


def illust_path(root: str, illust: dict, ext: str = "") -> str:
    """多页作品返回作品文件夹路径；单页作品返回图片文件路径（需提供 ext）。"""
    base = author_dir(root, illust["author_id"], illust.get("author_name") or "")
    name = illust_base_name(illust)
    if int(illust.get("page_count") or 1) > 1:
        return os.path.join(base, name)
    return os.path.join(base, name + (ext or ".jpg"))
