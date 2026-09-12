from dataclasses import dataclass, field

from .client import PixivClient, PixivRequestError


@dataclass
class NovelMeta:
    novel_id: str
    title: str
    author_id: str
    author_name: str
    description: str
    tags: list
    x_restrict: int
    create_date: str
    upload_date: str
    series_id: str = ""
    series_title: str = ""
    series_order: int = 0
    cover_url: str = ""
    text_count: int = 0
    embedded_images: dict = field(default_factory=dict)


@dataclass
class SeriesContent:
    series_id: str
    title: str
    novel_ids: list = field(default_factory=list)


def get_user_novel_ids(client: PixivClient, user_id: str) -> list:
    data = client.get_json(f"{client.BASE}/ajax/user/{user_id}/profile/all?lang=zh")
    body = data.get("body") or {}
    novels = body.get("novels") or {}
    if isinstance(novels, dict):
        raw = novels.keys()
    else:
        raw = novels
    ids = [str(nid) for nid in raw if str(nid).isdigit()]
    return sorted(ids, key=int)


def get_user_name(client: PixivClient, user_id: str) -> str:
    data = client.get_json(f"{client.BASE}/ajax/user/{user_id}?lang=zh")
    body = data.get("body") or {}
    return body.get("name") or ""


def get_novel_detail(client: PixivClient, novel_id: str) -> tuple:
    data = client.get_json(f"{client.BASE}/ajax/novel/{novel_id}?lang=zh")
    body = data.get("body") or {}
    if not body.get("id"):
        raise PixivRequestError("作品不存在或已被删除", kind="not_found")
    tags_raw = (body.get("tags") or {}).get("tags") or []
    tags = [t.get("tag", "") for t in tags_raw if isinstance(t, dict) and t.get("tag")]
    series = body.get("seriesNavData") or {}
    embedded = {}
    for img_id, img in (body.get("textEmbeddedImages") or {}).items():
        urls = (img or {}).get("urls") or {}
        url = urls.get("original") or urls.get("1200x1200") or ""
        if url:
            embedded[str(img_id)] = url
    content = body.get("content")
    if content is None:
        raise PixivRequestError("接口未返回正文内容", kind="parse")
    meta = NovelMeta(
        novel_id=str(body.get("id") or novel_id),
        title=body.get("title") or "",
        author_id=str(body.get("userId") or ""),
        author_name=body.get("userName") or "",
        description=body.get("description") or "",
        tags=tags,
        x_restrict=int(body.get("xRestrict") or 0),
        create_date=body.get("createDate") or "",
        upload_date=body.get("uploadDate") or body.get("createDate") or "",
        series_id=str(series.get("seriesId") or ""),
        series_title=series.get("title") or "",
        series_order=int(series.get("order") or 0),
        cover_url=body.get("coverUrl") or "",
        text_count=int(body.get("textCount") or len(content)),
        embedded_images=embedded,
    )
    return meta, content


def get_novel_meta(client: PixivClient, novel_id: str) -> NovelMeta:
    meta, _ = get_novel_detail(client, novel_id)
    return meta


def get_novel_content(client: PixivClient, novel_id: str) -> str:
    _, content = get_novel_detail(client, novel_id)
    return content


def get_series_novel_ids(client: PixivClient, series_id: str) -> SeriesContent:
    data = client.get_json(f"{client.BASE}/ajax/novel/series/{series_id}?lang=zh")
    body = data.get("body") or {}
    title = body.get("title") or ""
    total = int(body.get("total") or 0)
    ids = []
    last_order = 0
    while True:
        chunk = client.get_json(
            f"{client.BASE}/ajax/novel/series_content/{series_id}"
            f"?limit=30&last_order={last_order}&order_by=asc&lang=zh"
        )
        chunk_body = chunk.get("body") or {}
        contents = chunk_body.get("seriesContents") or (chunk_body.get("page") or {}).get("seriesContents") or []
        if not contents:
            break
        for item in contents:
            nid = str(item.get("id") or "")
            if nid.isdigit():
                ids.append(nid)
        last_order += len(contents)
        if len(contents) < 30 or (total and len(ids) >= total):
            break
    return SeriesContent(series_id=str(series_id), title=title, novel_ids=ids)


def get_insert_illusts(client: PixivClient, novel_id: str, illust_ids: list) -> dict:
    if not illust_ids:
        return {}
    query = "&".join(f"id%5B%5D={iid}" for iid in illust_ids)
    data = client.get_json(f"{client.BASE}/ajax/novel/{novel_id}/insert_illusts?{query}&lang=zh")
    return (data.get("body") or {})


def download_image(client: PixivClient, url: str) -> bytes:
    return client.download_binary(url, referer=f"{client.BASE}/")
