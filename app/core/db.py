import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from ..config import app_data_dir
from . import utils

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
        # 抓取跑在 worker 线程，界面同时在主线程读。sqlite3 的连接不能跨线程
        # 并发使用（并发 commit 会抛 "not an error" / "database is locked"），
        # 因此改为每线程一条连接；再配合 WAL 让读不阻塞写。
        self._local = threading.local()
        self._tls_lock = threading.Lock()
        self._lock = threading.RLock()   # 串行化写事务
        self._migrated = False
        self._conn = self._connection()  # 兼容旧代码对 self._conn 的直接引用

    # ---------------------------------------------------------- 连接管理
    def _connection(self) -> sqlite3.Connection:
        """取当前线程的连接（首次调用时创建）。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        except Exception:
            pass
        self._local.conn = conn
        # 建表只做一次，但要保证并发首次访问时不重复执行
        with self._tls_lock:
            if not self._migrated:
                self._migrate_on(conn)
                self._migrated = True
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        """任何线程访问都拿到自己的连接（供外部直接查询时使用）。"""
        return self._connection()

    def _query(self, sql: str, params=()):
        """读查询：立即物化成 dict，避免游标跨线程共享。"""
        conn = self._connection()
        with self._lock:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]

    def _query_one(self, sql: str, params=()):
        conn = self._connection()
        with self._lock:
            row = conn.execute(sql, tuple(params)).fetchone()
            return dict(row) if row else None

    def _execute(self, sql: str, params=()):
        conn = self._connection()
        with self._lock:
            cur = conn.execute(sql, tuple(params))
            conn.commit()
            return cur

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
        main_conn = getattr(self, "_conn", None)
        if main_conn is not None:
            try:
                main_conn.close()
            except Exception:
                pass

    def _migrate(self):
        """兼容旧调用：在默认连接上执行迁移。"""
        self._connection()

    def _migrate_on(self, conn: sqlite3.Connection):
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        if version > 0:
            backup = self.path + f".v{version}.bak"
            try:
                conn.commit()
                shutil.copy2(self.path, backup)
            except Exception:
                if self.logger:
                    self.logger.warning("索引备份失败：%s", backup, exc_info=True)
        if version < 1:
            conn.executescript(_SCHEMA_V1)
        if version < 2:
            conn.executescript(_SCHEMA_V2)
        if version < 3:
            conn.executescript(_SCHEMA_V3)
        if version < 4:
            conn.executescript(_SCHEMA_V4)
        if version < 5:
            conn.executescript(_SCHEMA_V5)
        if version < 6:
            conn.executescript(_SCHEMA_V6)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    # ---- authors ----
    def add_author(self, author_id: str, author_name: str = "", pending_validate: bool = False, kind: str = "novel") -> bool:
        conn = self._connection()
        try:
            conn.execute(
                "INSERT INTO authors(author_id, kind, author_name, pending_validate, created_at) VALUES (?,?,?,?,?)",
                (author_id, kind, author_name, int(pending_validate), self._now()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_author(self, author_id: str, kind: str = "novel"):
        self._execute("DELETE FROM authors WHERE author_id=? AND kind=?", (author_id, kind))

    def set_author_enabled(self, author_id: str, enabled: bool, kind: str = "novel",
                           reason: str = ""):
        """启用 / 停用作者。reason 用于区分「账号已注销」与「手动停用」，启用时自动清空。"""
        if enabled:
            self._execute(
                "UPDATE authors SET enabled=?, disabled_at=NULL, disabled_reason=NULL"
                " WHERE author_id=? AND kind=?",
                (int(enabled), author_id, kind))
        else:
            self._execute(
                "UPDATE authors SET enabled=?, disabled_at=?, disabled_reason=?"
                " WHERE author_id=? AND kind=?",
                (int(enabled), self._now(), reason or "", author_id, kind))

    def author_disable_reason(self, author_id: str, kind: str = "novel") -> str:
        row = self._query_one(
            "SELECT disabled_reason FROM authors WHERE author_id=? AND kind=?",
            (author_id, kind))
        return (row["disabled_reason"] or "") if row else ""

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
        return self._query(sql, params)

    def reenable_author(self, author_id: str, kind: str) -> None:
        self._execute(
            "UPDATE authors SET enabled=1, disabled_at=NULL WHERE author_id=? AND kind=?",
            (author_id, kind))

    def update_author_name(self, author_id: str, name: str, kind: str = None):
        """更新作者昵称。

        authors 的主键是 (author_id, kind)：同一位作者可以同时存在于小说名单和
        插画名单。不传 kind 时会把两条记录一起改掉，于是插画名单里的昵称被小说
        名单抓到的名字覆盖（反之亦然）。调用方能确定类型时应显式传 kind。
        """
        if kind:
            self._execute(
                "UPDATE authors SET author_name=?, pending_validate=0"
                " WHERE author_id=? AND kind=?", (name, author_id, kind))
        else:
            self._execute(
                "UPDATE authors SET author_name=?, pending_validate=0 WHERE author_id=?",
                (name, author_id))

    def author_counts_bulk(self, kind: str) -> dict:
        """一次查出某类名单全部作者的各项计数，替代逐作者查询（N+1）。

        返回 {author_id: {"novels": n, "deleted_novels": n, "deleted_series": n,
                          "deleted_total": n, "illusts": n, "deleted_illusts": n}}
        """
        out = {}
        conn = self._connection()
        with self._lock:
            if kind == "novel":
                # deleted_series 是「该作者名下被标删除的系列个数」，与作品行无关，
                # 不能按作品行求和（同一系列有多部作品时会被重复计数）。
                series_by_author = {}
                for r in conn.execute(
                        "SELECT author_id, COUNT(*) AS n FROM deleted_series"
                        " GROUP BY author_id").fetchall():
                    series_by_author[r["author_id"]] = int(r["n"] or 0)
                for r in conn.execute(
                        "SELECT author_id,"
                        " SUM(status IN ('ok','outdated')) AS novels,"
                        " SUM(status='deleted') AS deleted_novels,"
                        " SUM(status='deleted' OR (series_id != '' AND series_id IN"
                        "  (SELECT series_id FROM deleted_series WHERE"
                        "   deleted_series.author_id = novels.author_id))) AS deleted_total"
                        " FROM novels GROUP BY author_id").fetchall():
                    out[r["author_id"]] = {
                        "novels": int(r["novels"] or 0),
                        "deleted_novels": int(r["deleted_novels"] or 0),
                        "deleted_series": series_by_author.get(r["author_id"], 0),
                        "deleted_total": int(r["deleted_total"] or 0),
                    }
                # 作品全被删光的作者上面 GROUP BY 查不到，单独补上系列数
                for aid, n in series_by_author.items():
                    row = out.setdefault(aid, {
                        "novels": 0, "deleted_novels": 0,
                        "deleted_series": 0, "deleted_total": 0})
                    row["deleted_series"] = max(row["deleted_series"], n)
                    row["deleted_total"] = max(row["deleted_total"], n)
            else:
                for r in conn.execute(
                        "SELECT author_id, SUM(status='ok') AS illusts,"
                        " SUM(status='deleted') AS deleted_illusts"
                        " FROM illusts GROUP BY author_id").fetchall():
                    out[r["author_id"]] = {
                        "illusts": int(r["illusts"] or 0),
                        "deleted_illusts": int(r["deleted_illusts"] or 0),
                    }
        return out

    def touch_author_fetch(self, author_id: str, kind: str = "novel"):
        self._execute(
            "UPDATE authors SET last_fetch_at=? WHERE author_id=? AND kind=?",
            (self._now(), author_id, kind))

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
        return self._query(sql, params)

    def get_author(self, author_id: str, kind: str = "novel"):
        return self._query_one(
            "SELECT * FROM authors WHERE author_id=? AND kind=?", (author_id, kind))

    def _count(self, sql: str, params=()) -> int:
        row = self._query_one(sql, params)
        if not row:
            return 0
        return int(list(row.values())[0])

    def author_novel_count(self, author_id: str) -> int:
        return self._count(
            "SELECT COUNT(*) FROM novels WHERE author_id=? AND status IN ('ok','outdated')",
            (author_id,))

    def author_deleted_novel_count(self, author_id: str) -> int:
        return self._count(
            "SELECT COUNT(*) FROM novels WHERE author_id=? AND status='deleted'",
            (author_id,))

    def author_deleted_total(self, author_id: str) -> int:
        """总共删了多少小说：被标删除的单篇 + 已删系列里包含的作品（自动去重）。"""
        return self._count(
            "SELECT COUNT(*) FROM novels WHERE author_id=? AND "
            "(status='deleted' OR series_id IN "
            "(SELECT series_id FROM deleted_series WHERE author_id=?))",
            (author_id, author_id))

    # ---- novels ----
    def get_novel(self, novel_id: str):
        return self._query_one("SELECT * FROM novels WHERE novel_id=?", (novel_id,))

    def upsert_novel(self, rec: dict):
        # 整个「先查后写」必须在同一把锁内：否则两个线程同时判断 existing is None，
        # 会各插一次，触发主键冲突或游标错乱（bad parameter or other API misuse）。
        conn = self._connection()
        with self._lock:
            existing = self.get_novel(rec["novel_id"])
            if existing is None:
                rec.setdefault("first_seen_at", self._now())
                cols = ", ".join(rec.keys())
                marks = ", ".join("?" for _ in rec)
                conn.execute(f"INSERT INTO novels({cols}) VALUES ({marks})", tuple(rec.values()))
            else:
                rec.pop("novel_id", None)
                rec.pop("first_seen_at", None)
                if rec:
                    sets = ", ".join(f"{k}=?" for k in rec)
                    conn.execute(
                        f"UPDATE novels SET {sets} WHERE novel_id=?",
                        tuple(rec.values()) + (existing["novel_id"],))
            conn.commit()

    def set_novel_status(self, novel_id: str, status: str, error_reason: str = ""):
        self._execute(
            "UPDATE novels SET status=?, error_reason=?, last_fetch_at=? WHERE novel_id=?",
            (status, error_reason, self._now(), novel_id))

    def remove_novel(self, novel_id: str) -> None:
        """仅删除索引记录，绝不触碰本地已下载文件。"""
        self._execute("DELETE FROM novels WHERE novel_id=?", (novel_id,))

    def novels_by_author(self, author_id: str) -> list:
        return self._query(
            "SELECT * FROM novels WHERE author_id=? ORDER BY publish_date, novel_id", (author_id,))

    def ok_novels(self) -> list:
        return self._query(
            "SELECT * FROM novels WHERE status=? ORDER BY author_id, CAST(novel_id AS INTEGER)",
            ("ok",))

    def failed_novels(self) -> list:
        return self._query(
            "SELECT * FROM novels WHERE status IN ('failed_auth','failed_403','failed_network','failed_parse')"
            " ORDER BY last_fetch_at")

    def deleted_novels(self) -> list:
        return self._query(
            "SELECT * FROM novels WHERE status='deleted' ORDER BY last_fetch_at DESC")

    def series_ids_by_author(self, author_id: str) -> list:
        conn = self._connection()
        with self._lock:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT series_id FROM novels WHERE author_id=? AND series_id != ''",
                    (author_id,)).fetchall()
            ]

    def mark_series_deleted(self, series_id: str, author_id: str):
        self._execute(
            "INSERT OR REPLACE INTO deleted_series(series_id, author_id, marked_at) VALUES (?,?,?)",
            (series_id, author_id, self._now()))

    def unmark_series_deleted(self, series_id: str):
        self._execute("DELETE FROM deleted_series WHERE series_id=?", (series_id,))

    def deleted_series_ids(self, author_id: str) -> set:
        conn = self._connection()
        with self._lock:
            return {
                r[0]
                for r in conn.execute(
                    "SELECT series_id FROM deleted_series WHERE author_id=?", (author_id,)).fetchall()
            }

    def should_poll_series(self, series_id: str, recheck_days: int = None) -> bool:
        row = self._query_one("SELECT marked_at FROM deleted_series WHERE series_id=?", (series_id,))
        if not row:
            return True
        try:
            marked = datetime.fromisoformat(row["marked_at"])
        except ValueError:
            return True
        try:
            days = int(SERIES_RECHECK_DAYS if recheck_days is None else recheck_days)
        except (TypeError, ValueError):
            days = SERIES_RECHECK_DAYS
        days = max(1, min(365, days))
        return (datetime.now() - marked).days >= days

    def search_novels(self, keyword: str) -> list:
        # 归一化匹配（假名互转 / 全半角）。个人书库规模（几千条内）全表取回
        # 再在 Python 侧过滤完全够快；LIKE %...% 既吃不到索引，又会因为关键词
        # 假名写法与库中不一致而误筛掉，干脆不用它粗筛。
        kw_norm = utils.normalize_search_text(keyword)
        rows = self._query("SELECT * FROM novels ORDER BY publish_date DESC")
        if not kw_norm:
            return rows
        return [
            r for r in rows
            if utils.search_matches(r.get("title"), kw_norm)
            or utils.search_matches(r.get("author_name"), kw_norm)
            or utils.search_matches(r.get("tags"), kw_norm)
        ]

    # ---- illusts ----
    def get_illust(self, illust_id: str):
        return self._query_one("SELECT * FROM illusts WHERE illust_id=?", (illust_id,))

    def upsert_illust(self, rec: dict):
        conn = self._connection()
        with self._lock:
            existing = self.get_illust(rec["illust_id"])
            if existing is None:
                rec.setdefault("first_seen_at", self._now())
                cols = ", ".join(rec.keys())
                marks = ", ".join("?" for _ in rec)
                conn.execute(f"INSERT INTO illusts({cols}) VALUES ({marks})", tuple(rec.values()))
            else:
                rec.pop("illust_id", None)
                rec.pop("first_seen_at", None)
                if rec:
                    sets = ", ".join(f"{k}=?" for k in rec)
                    conn.execute(
                        f"UPDATE illusts SET {sets} WHERE illust_id=?",
                        tuple(rec.values()) + (existing["illust_id"],))
            conn.commit()

    def set_illust_status(self, illust_id: str, status: str, error_reason: str = ""):
        self._execute(
            "UPDATE illusts SET status=?, error_reason=?, last_fetch_at=? WHERE illust_id=?",
            (status, error_reason, self._now(), illust_id))

    def remove_illust(self, illust_id: str) -> None:
        """仅删除索引记录，绝不触碰本地已下载文件。"""
        self._execute("DELETE FROM illusts WHERE illust_id=?", (illust_id,))

    def illusts_by_author(self, author_id: str) -> list:
        return self._query(
            "SELECT * FROM illusts WHERE author_id=? ORDER BY publish_date, illust_id", (author_id,))

    def ok_illusts(self) -> list:
        return self._query(
            "SELECT * FROM illusts WHERE status=? ORDER BY author_id, CAST(illust_id AS INTEGER)",
            ("ok",))

    def failed_illusts(self) -> list:
        return self._query(
            "SELECT * FROM illusts WHERE status IN ('failed_auth','failed_403','failed_network','failed_parse')"
            " ORDER BY last_fetch_at")

    def deleted_illusts(self) -> list:
        return self._query(
            "SELECT * FROM illusts WHERE status='deleted' ORDER BY last_fetch_at DESC")

    def author_illust_count(self, author_id: str) -> int:
        return self._count(
            "SELECT COUNT(*) FROM illusts WHERE author_id=? AND status='ok'", (author_id,))

    def author_deleted_illust_count(self, author_id: str) -> int:
        return self._count(
            "SELECT COUNT(*) FROM illusts WHERE author_id=? AND status='deleted'", (author_id,))

    def deleted_series_count(self, author_id: str) -> int:
        return self._count(
            "SELECT COUNT(*) FROM deleted_series WHERE author_id=?", (author_id,))

    def search_illusts(self, keyword: str) -> list:
        kw_norm = utils.normalize_search_text(keyword)
        rows = self._query("SELECT * FROM illusts ORDER BY publish_date DESC")
        if not kw_norm:
            return rows
        return [
            r for r in rows
            if utils.search_matches(r.get("title"), kw_norm)
            or utils.search_matches(r.get("author_name"), kw_norm)
            or utils.search_matches(r.get("tags"), kw_norm)
        ]

    def stats(self) -> dict:
        conn = self._connection()
        with self._lock:
            rows = conn.execute("SELECT status, COUNT(*) FROM novels GROUP BY status").fetchall()
        return {r[0]: int(r[1]) for r in rows}
