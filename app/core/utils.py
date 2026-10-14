import html
import re

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_TZ_RE = re.compile(r"(?:[+-]\d{2}:?\d{2}|Z)$")

# 作者主页链接 / 旧版 member.php 链接中的数字 ID
_USER_URL_RE = re.compile(r"(?:pixiv\.net/users/|pixiv\.net/member(?:_illust)?\.php\?[^#]*\bid=)(\d+)",
                          re.IGNORECASE)
_PURE_ID_RE = re.compile(r"^\d+$")


def extract_author_id(text: str) -> str:
    """从「纯数字 ID」或「作者主页链接」中提取作者 ID；无法识别时返回空串。

    支持形式：
        104854837
        https://www.pixiv.net/users/104854837
        https://www.pixiv.net/users/104854837/novels
        https://www.pixiv.net/member.php?id=104854837
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    if _PURE_ID_RE.match(raw):
        return raw
    m = _USER_URL_RE.search(raw)
    return m.group(1) if m else ""


def format_datetime(value: str) -> str:
    if not value:
        return ""
    text = _TZ_RE.sub("", value.strip()).replace("T", " ")
    return text[:19] if len(text) >= 19 else text


def clean_description(raw: str) -> str:
    if not raw:
        return ""
    text = _BR_RE.sub("\n", raw)
    text = _TAG_RE.sub("", text)
    return html.unescape(text).strip()
