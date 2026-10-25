"""失败重试计数（retry_count）的单元测试。"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("PIXIV_DATA_DIR", tempfile.mkdtemp(prefix="pixiv_retry_"))

from app.core.db import Database, MAX_AUTO_RETRIES  # noqa: E402
from app.core.models import Status  # noqa: E402


def _mkdb():
    d = tempfile.mkdtemp()
    return Database(os.path.join(d, "r.db"))


def test_retry_count_starts_zero():
    db = _mkdb()
    db.upsert_novel({"novel_id": "1", "author_id": "a", "status": Status.FAILED_NETWORK})
    assert db.novel_retry_count("1") == 0


def test_bump_retry_increments():
    db = _mkdb()
    db.upsert_novel({"novel_id": "1", "author_id": "a", "status": Status.FAILED_NETWORK})
    assert db.bump_novel_retry("1") == 1
    assert db.bump_novel_retry("1") == 2
    assert db.novel_retry_count("1") == 2


def test_reset_retry_zeroes():
    db = _mkdb()
    db.upsert_novel({"novel_id": "1", "author_id": "a", "status": Status.FAILED_NETWORK})
    db.bump_novel_retry("1")
    db.bump_novel_retry("1")
    db.reset_novel_retry("1")
    assert db.novel_retry_count("1") == 0


def test_exhausted_novels_only_returns_over_limit():
    db = _mkdb()
    # 一部超过上限、一部未超、一部正常
    db.upsert_novel({"novel_id": "a", "author_id": "x", "status": Status.FAILED_NETWORK})
    for _ in range(MAX_AUTO_RETRIES):
        db.bump_novel_retry("a")
    db.upsert_novel({"novel_id": "b", "author_id": "x", "status": Status.FAILED_NETWORK})
    db.bump_novel_retry("b")  # 1 次，未超
    db.upsert_novel({"novel_id": "c", "author_id": "x", "status": Status.OK})

    exhausted = {n["novel_id"] for n in db.exhausted_novels()}
    assert "a" in exhausted
    assert "b" not in exhausted
    assert "c" not in exhausted


def test_schema_version_is_latest():
    assert db_schema_version() >= 9


def db_schema_version():
    # 通过新建一个库并读 PRAGMA 验证迁移到最新版本
    db = _mkdb()
    db.upsert_novel({"novel_id": "1", "author_id": "a", "status": Status.OK})
    row = db._query_one("PRAGMA user_version")
    return int(list(row.values())[0])


def test_mark_read_and_unread():
    db = _mkdb()
    db.upsert_novel({"novel_id": "1", "author_id": "a", "status": Status.OK})
    assert db.get_novel("1").get("read_at") == ""
    db.mark_novel_read("1", True)
    assert db.get_novel("1").get("read_at") != ""
    db.mark_novel_read("1", False)
    assert db.get_novel("1").get("read_at") == ""


def test_collections_crud():
    db = _mkdb()
    cid = db.create_collection("科幻短篇")
    assert cid > 0
    # 重名返回同一个 id
    assert db.create_collection("科幻短篇") == cid
    db.upsert_novel({"novel_id": "1", "author_id": "a", "title": "t", "status": Status.OK})
    db.add_to_collection(cid, "novel", "1")
    db.add_to_collection(cid, "novel", "1")  # 幂等
    items = db.collection_items(cid)
    assert len(items) == 1
    assert items[0]["novel_id"] == "1"
    db.remove_from_collection(cid, "novel", "1")
    assert db.collection_items(cid) == []
    db.delete_collection(cid)
    assert db.list_collections() == []
