import os
import shutil
import sqlite3
import time

from ..config import app_data_dir

SCHEMA_VERSION = 1

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
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    def close(self):
        self._conn.close()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    # ---- authors ----
    def add_author(self, author_id: str, author_name: str = "", pending_validate: bool = False) -> bool:
        try:
            self._conn.execute(
                "INSERT INTO authors(author_id, author_name, pending_validate, created_at) VALUES (?,?,?,?)",
                (author_id, author_name, int(pending_validate), self._now()),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_author(self, author_id: str):
        self._conn.execute("DELETE FROM authors WHERE author_id=?", (author_id,))
        self._conn.commit()

    def set_author_enabled(self, author_id: str, enabled: bool):
        self._conn.execute("UPDATE authors SET enabled=? WHERE author_id=?", (int(enabled), author_id))
        self._conn.commit()

    def update_author_name(self, author_id: str, name: str):
        self._conn.execute(
            "UPDATE authors SET author_name=?, pending_validate=0 WHERE author_id=?", (name, author_id)
        )
        self._conn.commit()

    def touch_author_fetch(self, author_id: str):
        self._conn.execute("UPDATE authors SET last_fetch_at=? WHERE author_id=?", (self._now(), author_id))
        self._conn.commit()

    def list_authors(self, enabled_only: bool = False) -> list:
        sql = "SELECT * FROM authors"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY created_at"
        return [dict(r) for r in self._conn.execute(sql).fetchall()]

    def author_novel_count(self, author_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM novels WHERE author_id=? AND status IN ('ok','outdated')",
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

    def series_ids_by_author(self, author_id: str) -> list:
        return [
            r[0]
            for r in self._conn.execute(
                "SELECT DISTINCT series_id FROM novels WHERE author_id=? AND series_id != ''", (author_id,)
            ).fetchall()
        ]

    def search_novels(self, keyword: str) -> list:
        like = f"%{keyword}%"
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM novels WHERE title LIKE ? OR author_name LIKE ? OR tags LIKE ? ORDER BY publish_date DESC",
                (like, like, like),
            ).fetchall()
        ]

    def stats(self) -> dict:
        rows = self._conn.execute("SELECT status, COUNT(*) FROM novels GROUP BY status").fetchall()
        return {r[0]: int(r[1]) for r in rows}
