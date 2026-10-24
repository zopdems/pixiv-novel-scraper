import html
import os
import re
import tempfile
import unicodedata

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_TZ_RE = re.compile(r"(?:[+-]\d{2}:?\d{2}|Z)$")

# 平假名 ↔ 片假名 互转表（用于检索时模糊匹配，见 normalize_search_text）
_HIRA_TO_KATA = str.maketrans(
    "ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞ"
    "ただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽ"
    "まみむめもゃやゅゆょよらりるれろゎわゐゑをん"
    "ゔゕゖ",
    "ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾ"
    "タダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポ"
    "マミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶ",
)
_KATA_TO_HIRA = str.maketrans(
    "ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾ"
    "タダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポ"
    "マミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶ",
    "ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞ"
    "ただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽ"
    "まみむめもゃやゅゆょよらりるれろゎわゐゑをん"
    "ゔゕゖ",
)

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


def atomic_write_bytes(path: str, data: bytes) -> None:
    """原子写入二进制文件：先写同目录临时文件，再 os.replace 覆盖。

    直接 open(path, "wb") 写入时若中途断网 / 关闭程序，会留下半截损坏文件；
    换成「临时文件 + 原子替换」，任何时刻磁盘上要么是旧文件、要么是完整新文件。
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def normalize_search_text(text: str) -> str:
    """把文本归一化，用于检索时的宽松匹配。

    - 平假名 / 片假名互转统一（记不准原文假名也能搜到）；
    - 全角 → 半角（英数符号）；
    - 转小写、去空白。
    """
    if not text:
        return ""
    # 片假名统一转平假名（日文里平假名是音读基底）
    s = text.translate(_KATA_TO_HIRA)
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"\s+", " ", s).strip()


def search_matches(haystack: str, keyword_norm: str) -> bool:
    """检索命中判断：对 haystack 也做同一套归一化后做子串匹配。"""
    if not keyword_norm:
        return True
    return keyword_norm in normalize_search_text(haystack or "")
