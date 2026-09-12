import html
import re
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(
    r"\[chapter:(?P<chapter>[^\[\]]*?)\]"
    r"|\[\[rb:(?P<rb_base>[^>\[\]]+?)\s*>\s*(?P<rb_text>[^\[\]]+?)\]\]"
    r"|\[jump:(?P<jump>\d+)\]"
    r"|\[pixivimage:(?P<illust>\d+)\]"
    r"|\[uploadedimage:(?P<uploaded>[^\[\]]+?)\]"
    r"|\[(?P<unknown>[a-zA-Z]+:[^\[\]]*?)\]"
)


@dataclass
class Chapter:
    file_name: str
    title: str
    body_xhtml: str
    page_no: int


@dataclass
class ConvertResult:
    chapters: list = field(default_factory=list)
    unknown_markers: list = field(default_factory=list)
    illust_ids: list = field(default_factory=list)


def _convert_page(page_text: str, page_no: int, total_pages: int, illust_files: dict, unknown: list) -> str:
    parts = []
    pos = 0
    for m in _TOKEN_RE.finditer(page_text):
        if m.start() > pos:
            parts.append(html.escape(page_text[pos:m.start()]))
        if m.group("chapter") is not None:
            parts.append(f"<h1>{html.escape(m.group('chapter').strip())}</h1>")
        elif m.group("rb_base") is not None:
            base = html.escape(m.group("rb_base").strip())
            ruby = html.escape(m.group("rb_text").strip())
            parts.append(f"<ruby>{base}<rt>{ruby}</rt></ruby>")
        elif m.group("jump") is not None:
            target = int(m.group("jump"))
            if 1 <= target <= total_pages:
                parts.append(f'<a href="page_{target:03d}.xhtml">第 {target} 页</a>')
            else:
                parts.append(html.escape(m.group(0)))
        elif m.group("illust") is not None or m.group("uploaded") is not None:
            illust_id = m.group("illust") or m.group("uploaded")
            file_name = illust_files.get(illust_id)
            if file_name:
                parts.append(f'<p class="illust"><img src="images/{html.escape(file_name)}" alt="illust-{illust_id}"/></p>')
            else:
                parts.append(html.escape(m.group(0)))
        else:
            unknown.append(m.group(0))
            parts.append(html.escape(m.group(0)))
        pos = m.end()
    if pos < len(page_text):
        parts.append(html.escape(page_text[pos:]))
    body = "".join(parts)
    paragraphs = [p.strip() for p in body.split("\n") if p.strip()]
    out = []
    for p in paragraphs:
        if p.startswith("<h1>") or p.startswith('<p class="illust">'):
            out.append(p)
        else:
            out.append(f"<p>{p}</p>")
    return "\n".join(out)


def convert_novel_markup(content: str, illust_files: dict = None) -> ConvertResult:
    illust_files = illust_files or {}
    pages = content.split("[newpage]")
    total = len(pages)
    result = ConvertResult()
    pixiv_ids = re.findall(r"\[pixivimage:(\d+)\]", content)
    uploaded_ids = re.findall(r"\[uploadedimage:([^\[\]]+?)\]", content)
    result.illust_ids = sorted(set(pixiv_ids + uploaded_ids), key=str)
    for i, page in enumerate(pages, start=1):
        file_name = f"page_{i:03d}.xhtml"
        heading = None
        ch_m = re.search(r"\[chapter:([^\[\]]*?)\]", page)
        if ch_m:
            heading = ch_m.group(1).strip()
        title = heading or f"第 {i} 页"
        body = _convert_page(page, i, total, illust_files, result.unknown_markers)
        result.chapters.append(Chapter(file_name=file_name, title=title, body_xhtml=body, page_no=i))
    return result
