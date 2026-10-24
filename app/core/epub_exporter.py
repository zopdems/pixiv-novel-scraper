import html
import os
import tempfile

from ebooklib import epub

from .utils import clean_description, format_datetime

_CSS = """
body { font-family: serif; line-height: 1.8; margin: 5%; }
h1 { font-size: 1.4em; margin: 1em 0 0.6em; }
p { margin: 0.6em 0; text-indent: 0; }
p.illust { text-align: center; }
p.illust img { max-width: 100%; }
ruby rt { font-size: 0.6em; }
div.desc { border: 1px solid #ccc; padding: 1em; margin: 1em 0; font-size: 0.9em; }
div.cover { text-align: center; margin: 0; }
div.cover img { max-width: 100%; max-height: 100%; }
"""

_IMG_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".gif": "image/gif", ".webp": "image/webp"}


def _default_opts() -> dict:
    return {
        "include_info_page": True,
        "include_cover": True,
        "include_description": True,
        "include_tags_meta": True,
        "include_source_url": True,
        "language": "ja",
    }


_DEFAULT_OPTS = _default_opts()


def epub_opts(settings=None) -> dict:
    """从 Settings 生成 EPUB 编排选项；settings 为 None 时返回历史默认行为。"""
    if settings is None:
        return dict(_DEFAULT_OPTS)

    def _get(key, default):
        try:
            v = getattr(settings, key, default)
        except Exception:
            v = default
        return default if v is None else v

    lang = str(_get("epub_language", "ja")).strip() or "ja"
    if len(lang) > 12:
        lang = lang[:12]

    return {
        "include_info_page": bool(_get("epub_include_info_page", True)),
        "include_cover": bool(_get("epub_include_cover", True)),
        "include_description": bool(_get("epub_include_description", True)),
        "include_tags_meta": bool(_get("epub_include_tags_meta", True)),
        "include_source_url": bool(_get("epub_include_source_url", True)),
        "language": lang,
    }


def build_epub(meta, chapters, images: dict, out_path: str, cover_bytes: bytes = None,
               cover_name: str = "cover.jpg", opts: dict = None) -> None:
    opts = opts or _DEFAULT_OPTS
    source_url = f"https://www.pixiv.net/novel/show.php?id={meta.novel_id}"
    book = epub.EpubBook()
    book.set_identifier(f"pixiv-novel-{meta.novel_id}")
    book.set_title(meta.title)
    book.set_language(opts["language"])
    book.add_author(meta.author_name)
    if opts["include_tags_meta"]:
        for tag in meta.tags:
            book.add_metadata("DC", "subject", tag)
    if opts["include_description"]:
        book.add_metadata("DC", "description", clean_description(meta.description))
    if opts["include_source_url"]:
        book.add_metadata("DC", "source", source_url)

    style = epub.EpubItem(uid="style", file_name="style/main.css", media_type="text/css",
                          content=_CSS.encode("utf-8"))
    book.add_item(style)

    for file_name, blob in images.items():
        ext = os.path.splitext(file_name)[1].lower()
        media_type = _IMG_MEDIA.get(ext, "image/jpeg")
        book.add_item(epub.EpubItem(file_name=f"images/{file_name}", media_type=media_type, content=blob))

    cover_page = None
    if cover_bytes and opts["include_cover"]:
        cover_ext = os.path.splitext(cover_name)[1].lower() or ".jpg"
        cover_media = _IMG_MEDIA.get(cover_ext, "image/jpeg")
        cover_item = epub.EpubItem(
            uid="cover-image", file_name=f"images/cover{cover_ext}",
            media_type=cover_media, content=cover_bytes,
        )
        cover_item.properties = ["cover-image"]
        book.add_item(cover_item)
        cover_page = epub.EpubHtml(title="封面", file_name="cover.xhtml", lang=opts["language"])
        cover_page.content = (
            f'<html><head></head><body><div class="cover">'
            f'<img src="images/cover{cover_ext}" alt="cover"/></div></body></html>'
        )
        cover_page.add_item(style)
        book.add_item(cover_page)

    desc_page = None
    if opts["include_info_page"]:
        parts = [f"<h1>{html.escape(meta.title)}</h1>",
                 f"<p>作者：{html.escape(meta.author_name)}</p>"]
        if opts["include_source_url"]:
            parts.append(f"<p>链接：<a href=\"{source_url}\">{source_url}</a></p>")
        if opts["include_description"]:
            desc_text = clean_description(meta.description)
            parts.append(f"<div class=\"desc\">{html.escape(desc_text).replace(chr(10), '<br/>')}</div>")
        parts.append(f"<p>标签：{html.escape(' / '.join(meta.tags))}</p>")
        parts.append(f"<p>发布：{html.escape(format_datetime(meta.create_date))}　"
                     f"更新：{html.escape(format_datetime(meta.upload_date))}</p>")
        desc_page = epub.EpubHtml(title="作品信息", file_name="info.xhtml", lang=opts["language"])
        desc_page.content = f"<html><head></head><body>{''.join(parts)}</body></html>"
        desc_page.add_item(style)
        book.add_item(desc_page)

    spine_items = ([cover_page] if cover_page else []) + ([desc_page] if desc_page else [])
    toc = []
    for ch in chapters:
        page = epub.EpubHtml(title=ch.title, file_name=ch.file_name, lang=opts["language"])
        page.content = f"<html><head></head><body>{ch.body_xhtml}</body></html>"
        page.add_item(style)
        book.add_item(page)
        spine_items.append(page)
        toc.append(page)

    book.toc = toc
    book.spine = ["nav"] + spine_items
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # 原子写：先写临时文件再替换，避免打包到一半被中断留下损坏的 epub
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(out_path) or ".",
                                    prefix=".tmp_", suffix=".epub")
    os.close(fd)
    try:
        epub.write_epub(tmp_path, book)
        os.replace(tmp_path, out_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
