import os
import re
import time

from .models import Status
from .utils import clean_description, format_datetime

_TAG_RE = re.compile(r"<[^>]+>")


def _plain(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _fmt_dt(value: str) -> str:
    return format_datetime(value)[:16]


def _entry_block(novel: dict, level: int) -> list:
    url = f"https://www.pixiv.net/novel/show.php?id={novel['novel_id']}"
    title = _plain(novel.get("title")) or novel["novel_id"]
    heading = "#" * level
    status_note = ""
    if novel.get("status") == Status.DELETED:
        status_note = "（远端已删除）"
    elif novel.get("status") == Status.SKIPPED:
        status_note = "（已跳过）"
    lines = [f"{heading} [{title}]({url}){status_note}", "", f"- 链接：{url}"]
    if novel.get("publish_date"):
        lines.append(f"- 发布时间：{_fmt_dt(novel['publish_date'])}")
    if novel.get("remote_updated_at"):
        lines.append(f"- 更新时间：{_fmt_dt(novel['remote_updated_at'])}")
    tags = (novel.get("tags") or "").replace(",", ", ").strip(", ")
    if tags:
        lines.append(f"- 标签：{tags}")
    desc = clean_description(novel.get("description") or "")
    if desc:
        lines.append("")
        lines.extend(f"> {line}".rstrip() for line in desc.split("\n"))
    return lines


def render_author_md(author: dict, novels: list, run_stat: dict = None) -> str:
    singles = sorted(
        [n for n in novels if not n.get("series_id")],
        key=lambda x: (x.get("publish_date") or "", x.get("novel_id") or ""),
    )
    series_map = {}
    for n in novels:
        if n.get("series_id"):
            series_map.setdefault((n["series_id"], n.get("series_title") or n["series_id"]), []).append(n)
    for items in series_map.values():
        items.sort(key=lambda x: (x.get("series_order") or 0, x.get("publish_date") or ""))

    name = author.get("author_name") or author["author_id"]
    aid = author["author_id"]
    lines = [
        f"# {name}",
        "",
        f"- Pixiv：https://www.pixiv.net/users/{aid}",
        f"- 收录作品：{len(novels)} 部",
    ]
    if run_stat:
        lines.append(
            "- 最近抓取：{}（新增 {}，更新 {}，远端删除 {}，失败 {}）".format(
                time.strftime("%Y-%m-%d %H:%M"),
                run_stat.get("new", 0), run_stat.get("updated", 0),
                run_stat.get("deleted", 0), run_stat.get("failed", 0),
            )
        )
    lines += [
        "",
        "## 单篇",
        "",
    ]
    if singles:
        for n in singles:
            lines += _entry_block(n, 3)
            lines.append("")
    else:
        lines.append("（暂无）")
        lines.append("")
    lines += ["## 系列", ""]
    if series_map:
        for (_, series_title), items in sorted(series_map.items(), key=lambda kv: kv[0][1]):
            lines += [f"### {series_title}", ""]
            for n in items:
                lines += _entry_block(n, 4)
                lines.append("")
    else:
        lines.append("（暂无）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_author_md(path: str, author: dict, novels: list, run_stat: dict = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    content = render_author_md(author, novels, run_stat)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)
