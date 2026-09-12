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
    for author in authors:
        aid, name = author["author_id"], author.get("author_name") or ""
        file_name = author_md_name(aid, name)
        unified = os.path.join(author_dir(settings.epub_root, aid, name), file_name)
        separate = os.path.join(settings.md_root, file_name) if settings.md_separate else ""
        src, dst = (unified, separate) if settings.md_separate else (separate_legacy(settings, aid), unified)
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


def separate_legacy(settings, author_id: str) -> str:
    if not settings.md_root:
        return ""
    if not os.path.isdir(settings.md_root):
        return ""
    prefix = f"{author_id}_"
    for entry in os.listdir(settings.md_root):
        if entry.startswith(prefix) and entry.endswith(".md"):
            return os.path.join(settings.md_root, entry)
    return ""
