import html
import os

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

_IMG_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}


def build_epub(meta, chapters, images: dict, out_path: str, cover_bytes: bytes = None, cover_name: str = "cover.jpg") -> None:
    source_url = f"https://www.pixiv.net/novel/show.php?id={meta.novel_id}"
    book = epub.EpubBook()
    book.set_identifier(f"pixiv-novel-{meta.novel_id}")
    book.set_title(meta.title)
    book.set_language("ja")
    book.add_author(meta.author_name)
    for tag in meta.tags:
        book.add_metadata("DC", "subject", tag)
    book.add_metadata("DC", "description", clean_description(meta.description))
    book.add_metadata("DC", "source", source_url)

    style = epub.EpubItem(uid="style", file_name="style/main.css", media_type="text/css", content=_CSS.encode("utf-8"))
    book.add_item(style)

    for file_name, blob in images.items():
        ext = os.path.splitext(file_name)[1].lower()
        media_type = _IMG_MEDIA.get(ext, "image/jpeg")
        book.add_item(epub.EpubItem(file_name=f"images/{file_name}", media_type=media_type, content=blob))

    cover_page = None
    if cover_bytes:
        cover_ext = os.path.splitext(cover_name)[1].lower() or ".jpg"
        cover_media = _IMG_MEDIA.get(cover_ext, "image/jpeg")
        cover_item = epub.EpubItem(
            uid="cover-image", file_name=f"images/cover{cover_ext}",
            media_type=cover_media, content=cover_bytes,
        )
        cover_item.properties = ["cover-image"]
        book.add_item(cover_item)
        cover_page = epub.EpubHtml(title="封面", file_name="cover.xhtml", lang="ja")
        cover_page.content = (
            f'<html><head></head><body><div class="cover">'
            f'<img src="images/cover{cover_ext}" alt="cover"/></div></body></html>'
        )
        cover_page.add_item(style)
        book.add_item(cover_page)

    desc_text = clean_description(meta.description)
    desc_html = (
        f"<h1>{html.escape(meta.title)}</h1>"
        f"<p>作者：{html.escape(meta.author_name)}</p>"
        f"<p>链接：<a href=\"{source_url}\">{source_url}</a></p>"
        f"<div class=\"desc\">{html.escape(desc_text).replace(chr(10), '<br/>')}</div>"
        f"<p>标签：{html.escape(' / '.join(meta.tags))}</p>"
        f"<p>发布：{html.escape(format_datetime(meta.create_date))}　更新：{html.escape(format_datetime(meta.upload_date))}</p>"
    )
    desc_page = epub.EpubHtml(title="作品信息", file_name="info.xhtml", lang="ja")
    desc_page.content = f"<html><head></head><body>{desc_html}</body></html>"
    desc_page.add_item(style)
    book.add_item(desc_page)

    spine_items = ([cover_page] if cover_page else []) + [desc_page]
    toc = []
    for ch in chapters:
        page = epub.EpubHtml(title=ch.title, file_name=ch.file_name, lang="ja")
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
    epub.write_epub(out_path, book)
