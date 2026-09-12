import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Settings
from app.pixiv import api
from app.pixiv.client import PixivClient
from app.pixiv.markup import convert_novel_markup
from app.core.epub_exporter import build_epub

client = PixivClient(Settings(), "")
meta, content = api.get_novel_detail(client, "18418657")
print("标题:", meta.title)
print("作者:", meta.author_name, meta.author_id)
print("标签:", meta.tags)
print("系列:", meta.series_id, meta.series_title, meta.series_order)
print("更新时间:", meta.upload_date)
print("正文长度:", len(content))
print("内嵌上传图:", list(meta.embedded_images.keys()))

converted = convert_novel_markup(content)
print("章节数:", len(converted.chapters))
print("未知标记:", converted.unknown_markers[:5])
print("插图引用:", converted.illust_ids)

markup_map = {}
images = {}
if converted.illust_ids:
    info = api.get_insert_illusts(client, meta.novel_id, converted.illust_ids)
    for iid in converted.illust_ids:
        url = meta.embedded_images.get(iid, "")
        if not url:
            entry = info.get(str(iid)) or {}
            urls = (entry.get("illust") or {}).get("urls") or {}
            url = urls.get("original") or (entry.get("illust") or {}).get("url") or ""
        if url:
            blob = api.download_image(client, url)
            ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
            fn = f"{iid}{ext}"
            markup_map[iid] = fn
            images[fn] = blob
            print("已下载插图:", fn, len(blob), "bytes")
    converted = convert_novel_markup(content, markup_map)

cover = api.download_image(client, meta.cover_url) if meta.cover_url else None
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_test.epub")
build_epub(meta, converted.chapters, images, out, cover)
print("EPUB 大小:", os.path.getsize(out))
with zipfile.ZipFile(out) as z:
    names = z.namelist()
    print("文件数:", len(names))
    page1 = z.read([n for n in names if "page_001" in n][0]).decode("utf-8")
    print("正文页预览:", page1[100:400])
client.close()
print("LIVE PIPELINE OK")
