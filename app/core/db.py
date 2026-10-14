import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta

from ..config import app_data_dir

SCHEMA_VERSION = 6

SERIES_RECHECK_DAYS = 30

# 作者停用原因
DISABLE_REASON_DEACTIVATED = "deactivated"  # 账号已注销 / 不存在（抓取时自动判定）
DISABLE_REASON_MANUAL = "manual"            # 用户手动停用

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS authors (
    author_id TEXT PRIMARY KEY,
    author_name TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    pending_validate INTEGER NOT NULL DEFAULT 0,
    last_fetch_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS novels (
    novel_id TEXT PRIMARY KEY,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    series_id TEXT NOT NULL DEFAULT '',
    series_title TEXT NOT NULL DEFAULT '',
    series_order INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    x_restrict INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    remote_updated_at TEXT NOT NULL DEFAULT '',
    publish_date TEXT NOT NULL DEFAULT '',
    local_path TEXT NOT NULL DEFAULT '',
    last_fetch_at TEXT,
    error_reason TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_novels_author ON novels(author_id);
CREATE INDEX IF NOT EXISTS idx_novels_series ON novels(series_id);
CREATE INDEX IF NOT EXISTS idx_novels_status ON novels(status);
"""

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS illusts (
    illust_id TEXT PRIMARY KEY,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    page_count INTEGER NOT NULL DEFAULT 1,
    tags TEXT NOT NULL DEFAULT '',
    x_restrict INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    remote_updated_at TEXT NOT NULL DEFAULT '',
    publish_date TEXT NOT NULL DEFAULT '',
    local_path TEXT NOT NULL DEFAULT '',
    last_fetch_at TEXT,
    error_reason TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_illusts_author ON illusts(author_id);
CREATE INDEX IF NOT EXISTS idx_illusts_status ON illusts(status);
"""

# V5：记录作者被停用的时间，用于「停用作者自动复查」
_SCHEMA_V5 = """
ALTER TABLE authors ADD COLUMN disabled_at TEXT;
"""

# V6：区分「账号已注销」与「手动停用」
# 迁移时对历史数据做尽力推断：
#   - 有 disabled_at（v5 之后停用）→ 多为抓取时自动判定的注销
#   - 无 disabled_at（v5 之前停用）→ 多为手动停用
# old 版本无法 100% 还原，用户可通过开关按钮重新写入准确原因。
_SCHEMA_V6 = """
ALTER TABLE authors ADD COLUMN disabled_reason TEXT;
UPDATE authors SET disabled_reason='deactivated'
 WHERE enabled=0 AND disabled_at IS NOT NULL AND disabled_reason IS NULL;
UPDATE authors SET disabled_reason='manual'
 WHERE enabled=0 AND disabled_at IS NULL AND disabled_reason IS NULL;
"""

_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS authors_v3 (
    author_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'novel',
    author_name TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    pending_validate INTEGER NOT NULL DEFAULT 0,
    last_fetch_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (author_id, kind)
);
INSERT OR IGNORE INTO authors_v3(author_id, author_name, enabled, pending_validate, last_fetch_at, created_at)
    SELECT author_id, author_name, enabled, pending_validate, last_fetch_at, created_at FROM authors;
DROP TABLE authors;
ALTER TABLE authors_v3 RENAME TO authors;
"""

_SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS deleted_series (
    series_id TEXT PRIMARY KEY,
    author_id TEXT NOT NULL,
    marked_at TEXT NOT NULL
);
"""


def db_path() -> str:
    return os.path.join(app_data_dir(), "index.db")


class Database:
    def __init__(self, path: str = None, logger=None):
        self.path = path or db_path()
        self.logger = logger
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self):
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        if version > 0:
            backup = self.path + f".v{version}.bak"
            try:
                self._conn.commit()
                shutil.copy2(self.path, backup)
            except Exception:
                if self.logger:
                    self.logger.warning("索引备份失败：%s", backup, exc_info=True)
        if version < 1:
            self._conn.executescript(_SCHEMA_V1)
        if version < 2:
            self._conn.executescript(_SCHEMA_V2)
        if version < 3:
            self._conn.executescript(_SCHEMA_V3)
        if version < 4:
            self._conn.executescript(_SCHEMA_V4)
        if version < 5:
            self._conn.executescript(_SCHEMA_V5)
        if version < 6:
            self._conn.executescript(_SCHEMA_V6)
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    def close(self):
        self._conn.close()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    # ---- authors ----
    def add_author(self, author_id: str, author_name: str = "", pending_validate: bool = False, kind: str = "novel") -> bool:
        try:
            self._conn.execute(
                "INSERT INTO authors(author_id, kind, author_name, pending_validate, created_at) VALUES (?,?,?,?,?)",
                (author_id, kind, author_name, int(pending_validate), self._now()),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_author(self, author_id: str, kind: str = "novel"):
        self._conn.execute("DELETE FROM authors WHERE author_id=? AND kind=?", (author_id, kind))
        self._conn.commit()

    def set_author_enabled(self, author_id: str, enabled: bool, kind: str = "novel",
                           reason: str = ""):
        """启用 / 停用作者。reason 用于区分「账号已注销」与「手动停用」，启用时自动清空。"""
        if enabled:
            self._conn.execute(
                "UPDATE authors SET enabled=?, disabled_at=NULL, disabled_reason=NULL"
                " WHERE author_id=? AND kind=?",
                (int(enabled), author_id, kind))
        else:
            self._conn.execute(
                "UPDATE authors SET enabled=?, disabled_at=?, disabled_reason=?"
                " WHERE author_id=? AND kind=?",
                (int(enabled), self._now(), reason or "", author_id, kind))
        self._conn.commit()

    def author_disable_reason(self, author_id: str, kind: str = "novel") -> str:
        row = self._conn.execute(
            "SELECT disabled_reason FROM authors WHERE author_id=? AND kind=?",
            (author_id, kind)).fetchone()
        return (row[0] or "") if row else ""

    def authors_due_for_recheck(self, recheck_days: int, kind: str = None) -> list:
        """被停用且已超过复查间隔的作者；recheck_days<=0 时返回空（即不复查）。"""
        try:
            days = int(recheck_days or 0)
        except (TypeError, ValueError):
            days = 0
        if days <= 0:
            return []
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        # 只复查「因账号注销被停用」的作者；手动停用的不自动恢复
        sql = ("SELECT * FROM authors WHERE enabled=0 AND disabled_at IS NOT NULL"
               " AND disabled_at<=? AND (disabled_reason IS NULL OR disabled_reason!=?)")
        params = [cutoff, DISABLE_REASON_MANUAL]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY disabled_at"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def reenable_author(self, author_id: str, kind: str) -> None:
        self._conn.execute(
            "UPDATE authors SET enabled=1, disabled_at=NULL WHERE author_id=? AND kind=?",
            (author_id, kind))
        self._conn.commit()

    def update_author_name(self, author_id: str, name: str):
        self._conn.execute(
            "UPDATE authors SET author_name=?, pending_validate=0 WHERE author_id=?", (name, author_id)
        )
        self._conn.commit()

    def touch_author_fetch(self, author_id: str, kind: str = "novel"):
        self._conn.execute(
            "UPDATE authors SET last_fetch_at=? WHERE author_id=? AND kind=?", (self._now(), author_id, kind)
        )
        self._conn.commit()

    def list_authors(self, enabled_only: bool = False, kind: str = None) -> list:
        sql = "SELECT * FROM authors"
        conds, params = [], []
        if enabled_only:
            conds.append("enabled=1")
        if kind:
            conds.append("kind=?")
            params.append(kind)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def get_author(self, author_id: str, kind: str = "novel"):
        row = self._conn.execute(
            "SELECT * FROM authors WHERE author_id=? AND kind=?", (author_id, kind)
        ).fetchone()
        return dict(row) if row else None

    def author_novel_count(self, author_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM novels WHERE author_id=? AND status IN ('ok','outdated')",
            (author_id,),
        ).fetchone()
        return int(row[0])

    def author_deleted_novel_count(self, author_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM novels WHERE author_id=? AND status='deleted'",
            (author_id,),
        ).fetchone()
        return int(row[0])

    # ---- novels ----
    def get_novel(self, novel_id: str):
        row = self._conn.execute("SELECT * FROM novels WHERE novel_id=?", (novel_id,)).fetchone()
        return dict(row) if row else None

    def upsert_novel(self, rec: dict):
        existing = self.get_novel(rec["novel_id"])
        if existing is None:
            rec.setdefault("first_seen_at", self._now())
            cols = ", ".join(rec.keys())
            marks = ", ".join("?" for _ in rec)
            self._conn.execute(f"INSERT INTO novels({cols}) VALUES ({marks})", tuple(rec.values()))
        else:
            rec.pop("novel_id", None)
            rec.pop("first_seen_at", None)
            if rec:
                sets = ", ".join(f"{k}=?" for k in rec)
                self._conn.execute(f"UPDATE novels SET {sets} WHERE novel_id=?", tuple(rec.values()) + (existing["novel_id"],))
        self._conn.commit()

    def set_novel_status(self, novel_id: str, status: str, error_reason: str = ""):
        self._conn.execute(
            "UPDATE novels SET status=?, error_reason=?, last_fetch_at=? WHERE novel_id=?",
            (status, error_reason, self._now(), novel_id),
        )
        self._conn.commit()

    def remove_novel(self, novel_id: str) -> None:
        """仅删除索引记录，绝不触碰本地已下载文件。"""
        self._conn.execute("DELETE FROM novels WHERE novel_id=?", (novel_id,))
        self._conn.commit()

    def novels_by_author(self, author_id: str) -> list:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM novels WHERE author_id=? ORDER BY publish_date, novel_id", (author_id,)
            ).fetchall()
        ]

    def ok_novels(self) -> list:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM novels WHERE status=? ORDER BY author_id, CAST(novel_id AS INTEGER)",
                ("ok",),
            ).fetchall()
        ]

    def failed_novels(self) -> list:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM novels WHERE status IN ('failed_auth','failed_403','failed_network','failed_parse')"
                " ORDER BY last_fetch_at"
            ).fetchall()
        ]

    def deleted_novels(self) -> list:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM novels WHERE status='deleted' ORDER BY last_fetch_at DESC"
            ).fetchall()
        ]

    def series_ids_by_author(self, author_id: str) -> list:
        return [
            r[0]
            for r in self._conn.execute(
                "SELECT DISTINCT series_id FROM novels WHERE author_id=? AND series_id != ''", (author_id,)
            ).fetchall()
        ]

    def mark_series_deleted(self, series_id: str, author_id: str):
        self._conn.execute(
            "INSERT OR REPLACE INTO deleted_series(series_id, author_id, marked_at) VALUES (?,?,?)",
            (series_id, author_id, self._now()),
        )
        self._conn.commit()

    def unmark_series_deleted(self, series_id: str):
        self._conn.execute("DELETE FROM deleted_series WHERE series_id=?", (series_id,))
        self._conn.commit()

    def deleted_series_ids(self, author_id: str) -> set:
        return {
            r[0]
            for r in self._conn.execute(
                "SELECT series_id FROM deleted_series WHERE author_id=?", (author_id,)
            ).fetchall()
        }

    def should_poll_series(self, series_id: str, recheck_days: int = None) -> bool:
        row = self._conn.execute(
            "SELECT marked_at FROM deleted_series WHERE series_id=?", (series_id,)
        ).fetchone()
        if not row:
            return True
        try:
            marked = datetime.fromisoformat(row[0])
        except ValueError:
            return True
        try:
            days = int(SERIES_RECHECK_DAYS if recheck_days is None else recheck_days)
        except (TypeError, ValueError):
            days = SERIES_RECHECK_DAYS
        days = max(1, min(365, days))
        return (datetime.now() - marked).days >= days

    def search_novels(self, keyword: str) -> list:
        like = f"%{keyword}%"
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM novels WHERE title LIKE ? OR author_name LIKE ? OR tags LIKE ? ORDER BY publish_date DESC",
                (like, like, like),
            ).fetchall()
        ]

    # ---- illusts ----
    def get_illust(self, illust_id: str):
        row = self._conn.execute("SELECT * FROM illusts WHERE illust_id=?", (illust_id,)).fetchone()
        return dict(row) if row else None

    def upsert_illust(self, rec: dict):
        existing = self.get_illust(rec["illust_id"])
        if existing is None:
            rec.setdefault("first_seen_at", self._now())
            cols = ", ".join(rec.keys())
            marks = ", ".join("?" for _ in rec)
            self._conn.execute(f"INSERT INTO illusts({cols}) VALUES ({marks})", tuple(rec.values()))
        else:
            rec.pop("illust_id", None)
            rec.pop("first_seen_at", None)
            if rec:
                sets = ", ".join(f"{k}=?" for k in rec)
                self._conn.execute(f"UPDATE illusts SET {sets} WHERE illust_id=?", tuple(rec.values()) + (existing["illust_id"],))
        self._conn.commit()

    def set_illust_status(self, illust_id: str, status: str, error_reason: str = ""):
        self._conn.execute(
            "UPDATE illusts SET status=?, error_reason=?, last_fetch_at=? WHERE illust_id=?",
            (status, error_reason, self._now(), illust_id),
        )
        self._conn.commit()

    def remove_illust(self, illust_id: str) -> None:
        """仅删除索引记录，绝不触碰本地已下载文件。"""
        self._conn.execute("DELETE FROM illusts WHERE illust_id=?", (illust_id,))
        self._conn.commit()

    def illusts_by_author(self, author_id: str) -> list:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM illusts WHERE author_id=? ORDER BY publish_date, illust_id", (author_id,)
            ).fetchall()
        ]

    def ok_illusts(self) -> list:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM illusts WHERE status=? ORDER BY author_id, CAST(illust_id AS INTEGER)",
                ("ok",),
            ).fetchall()
        ]

    def failed_illusts(self) -> list:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM illusts WHERE status IN ('failed_auth','failed_403','failed_network','failed_parse')"
                " ORDER BY last_fetch_at"
            ).fetchall()
        ]

    def deleted_illusts(self) -> list:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM illusts WHERE status='deleted' ORDER BY last_fetch_at DESC"
            ).fetchall()
        ]

    def author_illust_count(self, author_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM illusts WHERE author_id=? AND status='ok'",
            (author_id,),
        ).fetchone()
        return int(row[0])

    def author_deleted_illust_count(self, author_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM illusts WHERE author_id=? AND status='deleted'",
            (author_id,),
        ).fetchone()
        return int(row[0])

    def deleted_series_count(self, author_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM deleted_series WHERE author_id=?",
            (author_id,),
        ).fetchone()
        return int(row[0])

    def search_illusts(self, keyword: str) -> list:
        like = f"%{keyword}%"
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM illusts WHERE title LIKE ? OR author_name LIKE ? OR tags LIKE ? ORDER BY publish_date DESC",
                (like, like, like),
            ).fetchall()
        ]

    def stats(self) -> dict:
        rows = self._conn.execute("SELECT status, COUNT(*) FROM novels GROUP BY status").fetchall()
        return {r[0]: int(r[1]) for r in rows}
