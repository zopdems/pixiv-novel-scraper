"""排行榜数据层（独立 rank.db）。

设计要点：
- 与主库 index.db 分离：主库是「我下载了什么」（作品索引），rank.db 是「我观察到了什么」
  （时间序列日志）。两者生命周期不同，rank.db 可随时删除重建而不影响书库。
- 作品基础信息与上榜记录分离：同一部作品会连续多天在榜、也可能同时在多个榜单，
  重复存标题/作者会造成大量冗余。
- 派生指标（在榜天数、名次变化、收藏日增）一律现算不落表，避免冗余与不一致。
"""
import json
import os
import sqlite3
import time

RANK_DB_VERSION = 3

# 作品语言（接口 language 字段的实际取值）
LANG_JA = "ja"
LANG_ZH_CN = "zh-cn"      # 简体中文
LANG_OTHER = "other"

# 小说排行榜可用榜单（实测 rookie 为空、original 返回 400）
RANK_MODES = ["daily", "weekly", "monthly", "male", "female"]
R18_MODES = ["daily_r18", "male_r18", "female_r18"]
ALL_MODES = RANK_MODES + R18_MODES

MODE_LABELS = {
    "daily": "每日", "weekly": "每周", "monthly": "每月",
    "male": "男性向", "female": "女性向",
    "daily_r18": "每日 R-18", "male_r18": "男性向 R-18", "female_r18": "女性向 R-18",
}

_DEFAULT_MODES = ["daily", "weekly"]

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS rank_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    rank_date TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(mode, rank_date)
);
CREATE TABLE IF NOT EXISTS rank_works (
    work_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    author_id TEXT NOT NULL DEFAULT '',
    author_name TEXT NOT NULL DEFAULT '',
    series_id TEXT NOT NULL DEFAULT '',
    series_title TEXT NOT NULL DEFAULT '',
    word_count INTEGER NOT NULL DEFAULT 0,
    reading_time INTEGER NOT NULL DEFAULT 0,
    bookmark_count INTEGER NOT NULL DEFAULT 0,
    x_restrict INTEGER NOT NULL DEFAULT 0,
    ai_type INTEGER NOT NULL DEFAULT 0,
    genre TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    cover_url TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rank_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    rank_date TEXT NOT NULL,
    rank INTEGER NOT NULL,
    work_id TEXT NOT NULL,
    bookmark_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(mode, rank_date, rank)
);
CREATE INDEX IF NOT EXISTS idx_entries_lookup ON rank_entries(mode, rank_date);
CREATE INDEX IF NOT EXISTS idx_entries_work ON rank_entries(work_id);
CREATE INDEX IF NOT EXISTS idx_works_author ON rank_works(author_id);
"""

# V2：记录作品语言，用于「仅保留简体中文」过滤
_SCHEMA_V2 = """
ALTER TABLE rank_works ADD COLUMN language TEXT NOT NULL DEFAULT '';
"""

# V3：每轮抓取的流水账（回答「今天/某天到底抓了多少」）
# 跟快照数据的区别：快照是「榜单长什么样」，流水是「我这次干了什么」，
# 只有流水能告诉用户某天新下载了几部、失败了几部、跑了多久。
_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS rank_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    rank_date TEXT NOT NULL DEFAULT '',
    date_from TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    modes TEXT NOT NULL DEFAULT '',
    days INTEGER NOT NULL DEFAULT 0,
    days_ok INTEGER NOT NULL DEFAULT 0,
    collected INTEGER NOT NULL DEFAULT 0,
    downloaded INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    filtered_out INTEGER NOT NULL DEFAULT 0,
    csv_count INTEGER NOT NULL DEFAULT 0,
    elapsed REAL NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 1,
    message TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON rank_runs(started_at);
"""

# 任务来源（流水里「来源」列的取值）
SRC_MANUAL = "手动"
SRC_SCHEDULED = "定时"
SRC_BACKFILL = "补抓"
SRC_DOWNLOAD = "补下载"


def mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, mode)


def _fmt_delta(delta) -> str:
    """变化量显示：上升 +N、下降 -N、无变化 0、无对比（首次上榜）留空。"""
    if delta is None:
        return ""
    if delta > 0:
        return f"+{delta}"
    return str(delta)


def default_modes() -> list:
    return list(_DEFAULT_MODES)


class RankDatabase:
    """rank.db 访问封装。"""

    RUN_KEEP = 500      # 流水最多保留多少条；超出的最旧记录自动丢弃
    RUN_TXT_NAME = "排行榜抓取历史.txt"

    def __init__(self, path: str, logger=None):
        self.path = path
        self.logger = logger
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    # ---------------------------------------------------------------- 迁移
    def _migrate(self):
        try:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        except Exception:
            version = 0
        if version < 1:
            self._conn.executescript(_SCHEMA_V1)
        if version < 2:
            self._conn.executescript(_SCHEMA_V2)
        if version < 3:
            self._conn.executescript(_SCHEMA_V3)
        if version < RANK_DB_VERSION:
            self._conn.execute(f"PRAGMA user_version = {RANK_DB_VERSION}")
            self._conn.commit()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    # ---------------------------------------------------------------- 写入
    def save_snapshot(self, mode: str, rank_date: str, items: list) -> int:
        """写入一次榜单快照。items 为已归一化的作品列表（含 rank）。

        同一 (mode, rank_date) 重复调用会覆盖当次记录，便于补抓修正。
        """
        now = self._now()
        cur = self._conn.cursor()
        cur.execute("DELETE FROM rank_entries WHERE mode=? AND rank_date=?", (mode, rank_date))
        cur.execute("DELETE FROM rank_snapshots WHERE mode=? AND rank_date=?", (mode, rank_date))
        cur.execute(
            "INSERT INTO rank_snapshots(mode, rank_date, captured_at, item_count)"
            " VALUES(?,?,?,?)", (mode, rank_date, now, len(items)))
        for it in items:
            work_id = str(it.get("work_id") or "").strip()
            if not work_id:
                continue
            tags = it.get("tags") or []
            cur.execute(
                "INSERT INTO rank_works(work_id, title, author_id, author_name,"
                " series_id, series_title, word_count, reading_time, bookmark_count,"
                " x_restrict, ai_type, genre, tags, cover_url, language,"
                " first_seen_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(work_id) DO UPDATE SET"
                " title=excluded.title, author_id=excluded.author_id,"
                " author_name=excluded.author_name, series_id=excluded.series_id,"
                " series_title=excluded.series_title, word_count=excluded.word_count,"
                " reading_time=excluded.reading_time, bookmark_count=excluded.bookmark_count,"
                " x_restrict=excluded.x_restrict, ai_type=excluded.ai_type,"
                " genre=excluded.genre, tags=excluded.tags, cover_url=excluded.cover_url,"
                " language=excluded.language, updated_at=excluded.updated_at",
                (work_id, str(it.get("title") or ""), str(it.get("author_id") or ""),
                 str(it.get("author_name") or ""), str(it.get("series_id") or ""),
                 str(it.get("series_title") or ""), int(it.get("word_count") or 0),
                 int(it.get("reading_time") or 0), int(it.get("bookmark_count") or 0),
                 int(it.get("x_restrict") or 0), int(it.get("ai_type") or 0),
                 str(it.get("genre") or ""), json.dumps(tags, ensure_ascii=False),
                 str(it.get("cover_url") or ""), str(it.get("language") or ""), now, now))
            cur.execute(
                "INSERT INTO rank_entries(mode, rank_date, rank, work_id, bookmark_count)"
                " VALUES(?,?,?,?,?)",
                (mode, rank_date, int(it.get("rank") or 0), work_id,
                 int(it.get("bookmark_count") or 0)))
        self._conn.commit()
        return len(items)

    # ---------------------------------------------------------------- 查询
    def dates(self, mode: str, limit: int = 60) -> list:
        rows = self._conn.execute(
            "SELECT rank_date FROM rank_snapshots WHERE mode=?"
            " ORDER BY rank_date DESC LIMIT ?", (mode, limit)).fetchall()
        return [r[0] for r in rows]

    def latest_date(self, mode: str) -> str:
        rows = self.dates(mode, 1)
        return rows[0] if rows else ""

    def entries(self, mode: str, rank_date: str, limit: int = 100) -> list:
        """某榜单某天的完整名次，附带派生指标。

        派生字段：days_on_board（在榜天数，含当天）、rank_delta（较上一次名次变化，
        负数为上升）、bookmark_delta（较上一次收藏增量）。
        """
        rows = self._conn.execute(
            "SELECT e.rank, e.work_id, w.title, w.author_id, w.author_name,"
            " w.series_id, w.series_title, w.word_count, w.reading_time,"
            " e.bookmark_count, w.x_restrict, w.ai_type, w.genre, w.tags,"
            " w.cover_url, w.language"
            " FROM rank_entries e LEFT JOIN rank_works w ON w.work_id=e.work_id"
            " WHERE e.mode=? AND e.rank_date=? ORDER BY e.rank LIMIT ?",
            (mode, rank_date, limit)).fetchall()

        # 上一次快照（用于算变化）
        prev_date = ""
        for d in self._conn.execute(
                "SELECT rank_date FROM rank_snapshots WHERE mode=? AND rank_date<?"
                " ORDER BY rank_date DESC LIMIT 1", (mode, rank_date)).fetchall():
            prev_date = d[0]
        prev = {}
        if prev_date:
            for r in self._conn.execute(
                    "SELECT work_id, rank, bookmark_count FROM rank_entries"
                    " WHERE mode=? AND rank_date=?", (mode, prev_date)).fetchall():
                prev[r["work_id"]] = (r["rank"], r["bookmark_count"])

        board = {}
        for r in self._conn.execute(
                "SELECT work_id, COUNT(DISTINCT rank_date) AS n FROM rank_entries"
                " WHERE mode=? AND rank_date<=? GROUP BY work_id",
                (mode, rank_date)).fetchall():
            board[r["work_id"]] = r["n"]

        out = []
        for r in rows:
            item = dict(r)
            item["days_on_board"] = board.get(r["work_id"], 1)
            p = prev.get(r["work_id"])
            item["rank_delta"] = (p[0] - r["rank"]) if p else None   # 正数=名次上升
            item["bookmark_delta"] = (r["bookmark_count"] - p[1]) if p else None
            item["is_new"] = p is None
            try:
                item["tags"] = json.loads(r["tags"] or "[]")
            except Exception:
                item["tags"] = []
            out.append(item)
        return out

    def work_history(self, work_id: str, mode: str = None, limit: int = 200) -> list:
        sql = ("SELECT mode, rank_date, rank, bookmark_count FROM rank_entries"
               " WHERE work_id=?")
        params = [work_id]
        if mode:
            sql += " AND mode=?"
            params.append(mode)
        sql += " ORDER BY rank_date DESC, mode LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def authors(self, limit: int = 1000) -> list:
        """汇总榜单里出现过的作者（用于「哪些还没加入名单」）。

        字段：author_id / author_name / works（不同作品数）/ entries（累计上榜次数）
             / best_rank（历史最高名次）/ last_date（最近上榜日期）
        """
        rows = self._conn.execute(
            "SELECT w.author_id AS author_id, w.author_name AS author_name,"
            " COUNT(DISTINCT w.work_id) AS works, COUNT(e.id) AS entries,"
            " MIN(e.rank) AS best_rank, MAX(e.rank_date) AS last_date"
            " FROM rank_works w JOIN rank_entries e ON e.work_id = w.work_id"
            " WHERE w.author_id != ''"
            " GROUP BY w.author_id"
            " ORDER BY entries DESC, best_rank ASC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def snapshot_stats(self) -> dict:
        """每个榜单的快照数量与最新日期。"""
        out = {}
        for r in self._conn.execute(
                "SELECT mode, COUNT(*) AS n, MAX(rank_date) AS latest FROM rank_snapshots"
                " GROUP BY mode").fetchall():
            out[r["mode"]] = {"snapshots": r["n"], "latest": r["latest"]}
        return out

    def total_entries(self) -> int:
        try:
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM rank_entries").fetchone()[0])
        except Exception:
            return 0

    # ---------------------------------------------------------------- 抓取流水
    def add_run(self, **kw) -> int:
        """记录一轮抓取。失败不影响主流程（调用方已兜异常）。"""
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO rank_runs(started_at, finished_at, rank_date, date_from,"
            " source, modes, days, days_ok, collected, downloaded, skipped, failed,"
            " filtered_out, csv_count, elapsed, ok, message)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(kw.get("started_at") or self._now()),
             str(kw.get("finished_at") or self._now()),
             str(kw.get("rank_date") or ""),
             str(kw.get("date_from") or kw.get("rank_date") or ""),
             str(kw.get("source") or ""),
             str(kw.get("modes") or ""),
             int(kw.get("days") or 0),
             int(kw.get("days_ok") or 0),
             int(kw.get("collected") or 0),
             int(kw.get("downloaded") or 0),
             int(kw.get("skipped") or 0),
             int(kw.get("failed") or 0),
             int(kw.get("filtered_out") or 0),
             int(kw.get("csv_count") or 0),
             float(kw.get("elapsed") or 0),
             1 if kw.get("ok", True) else 0,
             str(kw.get("message") or "")[:500]))
        rid = cur.lastrowid or 0
        # 只留最近 RUN_KEEP 条，避免长期跑定时任务把库撑大
        cur.execute(
            "DELETE FROM rank_runs WHERE id NOT IN"
            " (SELECT id FROM rank_runs ORDER BY id DESC LIMIT ?)", (self.RUN_KEEP,))
        self._conn.commit()
        return rid

    def append_run_txt(self, **kw) -> str:
        """把本轮结果追加成一行人话，写到 logs 目录（跟 run.log 挨着）。

        与 rank_runs 表并存、各管一头：表给界面统计用，这份给人直接双击打开看，
        也补上了表「只在正常结束时才落一行」的短板之外的可读性。
        """
        from ..logging_setup import log_dir

        path = os.path.join(log_dir(), self.RUN_TXT_NAME)
        modes = "、".join(
            mode_label(x) for x in str(kw.get("modes") or "").split(",") if x) or "-"
        span = str(kw.get("rank_date") or "-")
        if int(kw.get("days") or 0) > 1 and kw.get("date_from"):
            span = f"{kw['date_from']} ~ {span}"
        nums = [f"收录 {int(kw.get('collected') or 0)} 条",
                f"新下载 {int(kw.get('downloaded') or 0)} 部"]
        if kw.get("skipped"):
            nums.append(f"跳过 {int(kw['skipped'])} 部")
        if kw.get("failed"):
            nums.append(f"失败 {int(kw['failed'])} 部")
        if kw.get("filtered_out"):
            nums.append(f"过滤非中文 {int(kw['filtered_out'])} 条")
        if int(kw.get("days") or 0) > 1:
            nums.append(f"{kw['days']} 天中 {int(kw.get('days_ok') or 0)} 天有数据")
        nums.append(f"耗时 {float(kw.get('elapsed') or 0):.0f}s")
        tail = ""
        if not kw.get("ok", True):
            tail += "  【有异常】"
        msg = str(kw.get("message") or "").strip()
        if msg:
            tail += f"  {msg[:200]}"
        line = " | ".join([str(kw.get("started_at") or ""), str(kw.get("source") or "-"),
                           modes, span]) + " | " + "，".join(nums) + tail
        with open(path, "a", encoding="utf-8") as f:
            if f.tell() == 0:   # 新文件先写两行说明，省得打开时不知道列是什么
                f.write("# 排行榜抓取历史：每轮一行，与软件里「排行榜 → 抓取历史」内容一致\n")
                f.write("# 开始时间 | 来源 | 榜单 | 榜单日期 | 明细\n\n")
            f.write(line + "\n")
        return path

    def runs(self, limit: int = 200) -> list:
        """最近若干轮抓取，新的在前。"""
        rows = self._conn.execute(
            "SELECT * FROM rank_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def run_totals(self) -> dict:
        """流水累计：跑了几轮、收录多少、新下载多少、失败多少、总耗时。"""
        r = self._conn.execute(
            "SELECT COUNT(*) AS rounds,"
            " COALESCE(SUM(collected),0) AS collected,"
            " COALESCE(SUM(downloaded),0) AS downloaded,"
            " COALESCE(SUM(skipped),0) AS skipped,"
            " COALESCE(SUM(failed),0) AS failed,"
            " COALESCE(SUM(elapsed),0) AS elapsed FROM rank_runs").fetchone()
        return dict(r) if r else {}

    def clear_runs(self) -> int:
        """清空抓取历史（只清流水，不动榜单数据）。"""
        cur = self._conn.cursor()
        cur.execute("DELETE FROM rank_runs")
        n = cur.rowcount or 0
        self._conn.commit()
        return n

    def export_runs_csv(self, path: str, limit: int = 500) -> int:
        """导出抓取历史为 CSV（UTF-8 BOM，Excel 直接打开不乱码）。"""
        import csv

        rows = self.runs(limit)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["开始时间", "结束时间", "来源", "榜单", "榜单日期",
                        "补抓起始", "天数", "有数据天数", "收录条数", "新下载",
                        "跳过", "失败", "过滤非中文", "CSV份数", "耗时(秒)",
                        "结果", "说明"])
            for r in rows:
                modes = "、".join(
                    mode_label(x) for x in str(r.get("modes") or "").split(",") if x)
                w.writerow([
                    r.get("started_at") or "",
                    r.get("finished_at") or "",
                    r.get("source") or "",
                    modes,
                    r.get("rank_date") or "",
                    r.get("date_from") or "",
                    r.get("days") or 0,
                    r.get("days_ok") or 0,
                    r.get("collected") or 0,
                    r.get("downloaded") or 0,
                    r.get("skipped") or 0,
                    r.get("failed") or 0,
                    r.get("filtered_out") or 0,
                    r.get("csv_count") or 0,
                    r.get("elapsed") or 0,
                    "成功" if r.get("ok") else "有异常",
                    r.get("message") or "",
                ])
        return len(rows)

    # ---------------------------------------------------------------- 导出
    def export_csv(self, mode: str, rank_date: str, path: str, limit: int = 100) -> int:
        """导出某榜某天为 CSV（UTF-8 BOM，Excel 双击可直接打开不乱码）。"""
        import csv

        rows = self.entries(mode, rank_date, limit)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["名次", "名次变化", "收藏日增", "在榜天数", "标题", "作者",
                        "作者ID", "作品ID", "语言", "字数", "收藏数", "标签", "所属系列",
                        "R-18", "作品链接"])
            for r in rows:
                w.writerow([
                    r.get("rank"),
                    _fmt_delta(r.get("rank_delta")),
                    _fmt_delta(r.get("bookmark_delta")),
                    r.get("days_on_board") or 0,
                    r.get("title") or "",
                    r.get("author_name") or "",
                    r.get("author_id") or "",
                    r.get("work_id") or "",
                    r.get("language") or "",
                    r.get("word_count") or 0,
                    r.get("bookmark_count") or 0,
                    "、".join(r.get("tags") or []),
                    r.get("series_title") or "",
                    "是" if r.get("x_restrict") else "",
                    f"https://www.pixiv.net/novel/show.php?id={r.get('work_id')}",
                ])
        return len(rows)

    def export_json(self, mode: str, rank_date: str, path: str, limit: int = 100) -> int:
        """导出某榜某天为 JSON（含全部字段，便于程序处理）。"""
        rows = self.entries(mode, rank_date, limit)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"mode": mode, "rank_date": rank_date, "count": len(rows),
                       "exported_at": self._now(), "items": rows},
                      f, ensure_ascii=False, indent=2)
        return len(rows)

    def export_work_history_csv(self, work_id: str, path: str) -> int:
        """导出某部作品的全部上榜轨迹为 CSV。"""
        import csv

        rows = self.work_history(work_id)
        title = ""
        author = ""
        try:
            r = self._conn.execute(
                "SELECT title, author_name FROM rank_works WHERE work_id=?",
                (work_id,)).fetchone()
            if r:
                title, author = r["title"] or "", r["author_name"] or ""
        except Exception:
            pass
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([f"作品：{title}", f"作者：{author}", f"ID：{work_id}"])
            w.writerow([])
            w.writerow(["日期", "榜单", "名次", "收藏数"])
            for r in rows:
                w.writerow([r["rank_date"], mode_label(r["mode"]),
                            r["rank"], r["bookmark_count"]])
        return len(rows)

    def prune_before(self, rank_date: str) -> int:
        """删除指定日期之前的快照与记录（不含该日期），返回删除的快照数。"""
        cur = self._conn.cursor()
        cur.execute("DELETE FROM rank_entries WHERE rank_date<?", (rank_date,))
        cur.execute("DELETE FROM rank_snapshots WHERE rank_date<?", (rank_date,))
        n = cur.rowcount or 0
        # 清理已无任何上榜记录的作品
        cur.execute("DELETE FROM rank_works WHERE work_id NOT IN"
                    " (SELECT work_id FROM rank_entries)")
        self._conn.commit()
        return n
