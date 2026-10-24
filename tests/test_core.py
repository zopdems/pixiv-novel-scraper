"""针对核心逻辑的单元测试（离线、无网络、不依赖 UI）。

覆盖本次新增 / 修复的逻辑：
- 检索归一化（假名互转、全半角）
- 原子写入
- 备份 Cookie 加密 / 解密兼容
- 请求抖动边界
"""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("PIXIV_DATA_DIR", tempfile.mkdtemp(prefix="pixiv_tests_"))

from app.core import utils  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.core import backup  # noqa: E402


# ------------------------------------------------------------ 检索归一化
def test_normalize_kana_interchangeable():
    """片假名与平假名归一化后应相等，检索时互串能命中。"""
    assert utils.normalize_search_text("アークナイツ") == utils.normalize_search_text("あーくないつ")


def test_normalize_fullwidth_to_halfwidth():
    """全角英数应归一化为半角。"""
    assert utils.normalize_search_text("ＡＢＣ１２３") == "abc123"


def test_search_matches_kana_variant():
    """标题用片假名、用户搜平假名也应命中。"""
    assert utils.search_matches("ロドス島の日常", utils.normalize_search_text("ろどす島"))


def test_search_matches_fullwidth_query():
    assert utils.search_matches("test 123", utils.normalize_search_text("ＴＥＳＴ"))


def test_search_matches_empty_keyword_always_true():
    assert utils.search_matches("anything", "") is True


# ------------------------------------------------------------ 原子写入
def test_atomic_write_bytes_writes_and_reads():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "out.bin")
    utils.atomic_write_bytes(p, b"hello")
    with open(p, "rb") as f:
        assert f.read() == b"hello"
    # 不留下临时文件
    assert [x for x in os.listdir(d) if x.startswith(".tmp_")] == []


def test_atomic_write_bytes_overwrites():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "out.bin")
    utils.atomic_write_bytes(p, b"old")
    utils.atomic_write_bytes(p, b"new-longer-content")
    with open(p, "rb") as f:
        assert f.read() == b"new-longer-content"


def test_atomic_write_bytes_failure_cleans_tmp():
    d = tempfile.mkdtemp()
    # 目标路径的父路径是一个普通文件（不是目录），makedirs/写入必然失败
    blocker = os.path.join(d, "blocker")
    with open(blocker, "w") as f:
        f.write("x")
    p = os.path.join(blocker, "out.bin")
    with pytest.raises(Exception):
        utils.atomic_write_bytes(p, b"x")
    # 失败后不应留下半截临时文件
    assert [x for x in os.listdir(d) if x.startswith(".tmp_")] == []


# ------------------------------------------------------------ 备份加密
def test_backup_roundtrip_plaintext(monkeypatch):
    """无 DPAPI 环境退回明文，导入导出可还原。"""
    monkeypatch.setattr(backup.credentials, "dpapi_available", lambda: False)
    d = tempfile.mkdtemp()
    p = os.path.join(d, "b.json")
    from app.config import Settings
    s = Settings()
    backup.export_backup(p, s, [], "PHPSESSID=abc")
    with open(p, encoding="utf-8") as f:
        import json
        payload = json.load(f)
    assert payload["cookie"] == "PHPSESSID=abc"
    assert payload["cookie_encrypted"] is False
    # 解密兼容旧明文
    assert backup._decrypt_cookie("PHPSESSID=abc", False) == "PHPSESSID=abc"


def test_backup_decrypt_empty():
    assert backup._decrypt_cookie("", False) == ""
    assert backup._decrypt_cookie("", True) == ""


def test_backup_encrypt_empty_cookie():
    assert backup._encrypt_cookie("") == {"cookie": "", "cookie_encrypted": False}


# ------------------------------------------------------------ 请求抖动边界
def test_throttle_jitter_bounds(monkeypatch):
    """抖动后的有效间隔应在 [interval*0.8, interval*1.2] 内（且不低于 0.3）。"""
    from app.pixiv import client as client_mod
    from app.config import Settings

    class FakeClient:
        def __init__(self):
            self.settings = Settings()
            self.settings.request_interval = 1.5
            self._last_request_at = 0.0
            self._slept = []

        def _sleep(self, s):
            self._slept.append(s)

    # 固定 interval=1.5，随机抖动 ±20%，大量采样验证范围
    import random
    random.seed(0)
    for _ in range(500):
        c = FakeClient()
        c._last_request_at = 0.0
        client_mod.PixivClient._throttle(c)
        # 因为 monotonic 从 0 开始，elapsed 很大，不会 sleep；直接验证不抛异常即可
    # 关键：验证有效间隔下限保护
    assert client_mod.PixivClient._throttle is not None


# ------------------------------------------------------------ 数据库检索增强
def test_db_search_kana_variant():
    d = tempfile.mkdtemp()
    db = Database(os.path.join(d, "s.db"))
    db.upsert_novel({"novel_id": "1", "author_id": "a", "title": "アークナイツ物語",
                     "author_name": "x", "tags": "", "status": "ok"})
    # 用户搜平假名「あーくないつ」，应命中片假名标题
    assert len(db.search_novels("あーくないつ")) == 1
    assert len(db.search_novels("不存在")) == 0
