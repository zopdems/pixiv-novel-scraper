import html
import re

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_TZ_RE = re.compile(r"(?:[+-]\d{2}:?\d{2}|Z)$")


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
