"""「排行榜」页面（作为 MainWindow 的混入类，避免 main_window.py 继续膨胀）。"""
import logging
import queue
import time
import webbrowser

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit,
                               QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QInputDialog, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QTableView,
                               QVBoxLayout)

from ..core.rank_db import (SRC_BACKFILL, SRC_MANUAL, ALL_MODES, R18_MODES,
                            RANK_MODES, mode_label)
from .theme import current_mode, tokens
from .worker import RankingWorker


class _RankLogHandler(logging.Handler):
    """把「排行榜任务进行期间」的日志转到页面日志框。

    只在任务期间 active，避免把主抓取的日志也混进来。
    日志来自工作线程，因此先进队列，再由界面线程的定时器取走（不能直接跨线程操作控件）。
    """

    def __init__(self, sink):
        super().__init__()
        self.sink = sink
        self.active = False
        # 注意：logging 的格式串只认 %(asctime)s 这类，不能直接写 %H:%M:%S
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        if not self.active:
            return
        try:
            try:
                line = self.format(record)
            except Exception:
                line = record.getMessage()   # 格式串出问题时至少把原文显示出来
            self.sink(line)
        except Exception:
            pass


class _SortItem(QTableWidgetItem):
    """支持表头点击排序的单元格：带数值就按数值比，空值排最后。"""

    def __init__(self, text="", value=None):
        super().__init__(str(text))
        self._value = value

    def __lt__(self, other):
        a, b = self._value, getattr(other, "_value", None)
        if a is None and b is None:
            return self.text() < other.text()
        if a is None:
            return False
        if b is None:
            return True
        try:
            return float(a) < float(b)
        except (TypeError, ValueError):
            return self.text() < other.text()


class RankingPageMixin:
    # ---------------------------------------------------------------- 页面
    def _build_ranking_page(self):
        page, outer, _head = self._page(
            "排行榜", "抓取小说排行榜前一百名，记录名次变化；可按需把作品正文收进本地书库")

        bar, bl = self._card(margin=12, spacing=8)
        row = QHBoxLayout()
        row.setSpacing(8)

        self.rank_mode_combo = QComboBox()
        for m in ALL_MODES:
            self.rank_mode_combo.addItem(f"{mode_label(m)}（{m}）", m)
        # 窗口缩小时下拉框会被挤到看不清，按最长条目自适应并设下限
        self.rank_mode_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.rank_mode_combo.setMinimumContentsLength(8)
        self.rank_mode_combo.setMinimumWidth(150)
        self.rank_mode_combo.currentIndexChanged.connect(self._on_rank_mode_changed)

        self.rank_view_combo = QComboBox()
        self.rank_view_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.rank_view_combo.setMinimumWidth(110)

        self.rank_date_edit = QDateEdit()
        self.rank_date_edit.setCalendarPopup(True)
        self.rank_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.rank_date_edit.setDate(QDate.currentDate())
        self.rank_date_edit.setMinimumWidth(130)
        self.rank_date_edit.setFixedHeight(34)
        cal = self.rank_date_edit.calendarWidget()
        cal.setGridVisible(True)
        cal.resize(360, 300)
        # 配色已在主题里统一接管（QCalendarWidget 段）；这里再兜一道：
        # 万一以后有人给 QTableView::item 加了内边距，日期数字也不会被挤掉。
        view = cal.findChild(QTableView)
        if view is not None:
            view.setStyleSheet("QTableView::item { padding: 0px; }")
        self.rank_date_edit.setToolTip("选过去的日期再点「抓取榜单」即可补抓那一天的榜单")
        self.rank_date_edit.dateChanged.connect(self.refresh_ranking_table)

        self.btn_rank_fetch = QPushButton("抓取榜单")
        self.btn_rank_fetch.setProperty("class", "primary")
        self.btn_rank_fetch.clicked.connect(self.fetch_ranking)
        self.btn_rank_backfill = QPushButton("补抓历史")
        self.btn_rank_backfill.setToolTip(
            "往前连续补抓最近 N 天的「当前榜单」（每天 2 次请求，可随时中止）")
        self.btn_rank_backfill.clicked.connect(self.backfill_ranking)
        self.btn_rank_stop = QPushButton("中止")
        self.btn_rank_stop.setProperty("class", "danger")
        self.btn_rank_stop.setEnabled(False)
        self.btn_rank_stop.clicked.connect(self.stop_ranking)

        row.addWidget(QLabel("榜单"))
        row.addWidget(self.rank_mode_combo)
        row.addWidget(QLabel("日期"))
        row.addWidget(self.rank_date_edit)
        row.addStretch(1)
        row.addWidget(self.btn_rank_fetch)
        row.addWidget(self.btn_rank_backfill)
        row.addWidget(self.btn_rank_stop)
        bl.addLayout(row)

        self.rank_busy = QProgressBar()
        self.rank_busy.setRange(0, 0)
        self.rank_busy.setTextVisible(False)
        self.rank_busy.setVisible(False)
        bl.addWidget(self.rank_busy)

        self.rank_summary = QLabel("尚未抓取过榜单。点「抓取榜单」开始。")
        self.rank_summary.setProperty("class", "hint")
        self.rank_summary.setWordWrap(True)
        bl.addWidget(self.rank_summary)

        # 常驻的「上次榜单任务」结果，切走再回来也看得到
        self.rank_last_label = QLabel("尚未执行过榜单抓取")
        self.rank_last_label.setProperty("class", "hint")
        self.rank_last_label.setWordWrap(True)
        bl.addWidget(self.rank_last_label)

        log_head = QHBoxLayout()
        log_head.setSpacing(8)
        title = QLabel("任务日志")
        title.setProperty("class", "section-title")
        log_head.addWidget(title)
        log_head.addStretch(1)
        btn_clear_log = QPushButton("清空")
        btn_clear_log.clicked.connect(self.clear_rank_log)
        log_head.addWidget(btn_clear_log)
        bl.addLayout(log_head)

        self.rank_log_view = QPlainTextEdit()
        self.rank_log_view.setProperty("class", "log")
        self.rank_log_view.setReadOnly(True)
        self.rank_log_view.setMaximumBlockCount(2000)
        self.rank_log_view.setFixedHeight(130)
        self.rank_log_view.setPlaceholderText(
            "抓取过程实时显示在这里：抓了哪个榜、多少条、哪篇下载失败。")
        bl.addWidget(self.rank_log_view)
        outer.addWidget(bar)

        table_card, tl = self._card(margin=12, spacing=8)

        view_row = QHBoxLayout()
        view_row.setSpacing(8)
        view_row.addWidget(QLabel("视图"))
        self.rank_view_combo.addItem("作品榜", "works")
        self.rank_view_combo.addItem("榜单作者", "authors")
        self.rank_view_combo.addItem("抓取历史", "runs")
        self.rank_view_combo.currentIndexChanged.connect(self._on_rank_view_changed)
        view_row.addWidget(self.rank_view_combo)
        view_row.addStretch(1)

        # —— 作品榜视图的操作 ——
        self.btn_rank_download = QPushButton("下载所选正文")
        self.btn_rank_download.clicked.connect(self.download_selected_rank_work)
        view_row.addWidget(self.btn_rank_download)
        self.btn_rank_add_author = QPushButton("作者入名单")
        self.btn_rank_add_author.setToolTip("把选中作品对应的作者加入小说抓取名单")
        self.btn_rank_add_author.clicked.connect(self.add_rank_author)
        view_row.addWidget(self.btn_rank_add_author)
        self.btn_rank_export = QPushButton("导出本榜")
        self.btn_rank_export.setToolTip("把当前榜单导出为 CSV（Excel 可直接打开）或 JSON")
        self.btn_rank_export.clicked.connect(self.export_ranking)
        view_row.addWidget(self.btn_rank_export)
        self.btn_rank_history = QPushButton("上榜轨迹")
        self.btn_rank_history.setToolTip("查看所选作品历次上榜的名次记录")
        self.btn_rank_history.clicked.connect(self.show_rank_history)
        view_row.addWidget(self.btn_rank_history)

        # —— 榜单作者视图的操作 ——
        self.rank_only_new_author = QCheckBox("只看未加入名单的作者")
        self.rank_only_new_author.setChecked(True)
        self.rank_only_new_author.setToolTip("只显示从榜单抓到、但还没加入每日自动抓取名单的作者")
        self.rank_only_new_author.stateChanged.connect(self.refresh_ranking_authors)
        view_row.addWidget(self.rank_only_new_author)

        self.btn_rank_add_authors = QPushButton("加入名单")
        self.btn_rank_add_authors.setToolTip("把选中的作者加入小说抓取名单")
        self.btn_rank_add_authors.clicked.connect(self.add_selected_rank_authors)
        view_row.addWidget(self.btn_rank_add_authors)

        self.btn_rank_open_author = QPushButton("打开主页")
        self.btn_rank_open_author.clicked.connect(self.open_rank_author_page)
        view_row.addWidget(self.btn_rank_open_author)

        # —— 抓取历史视图的操作 ——
        self.btn_rank_runs_export = QPushButton("导出历史")
        self.btn_rank_runs_export.setToolTip("把抓取历史导出为 CSV（Excel 可直接打开）")
        self.btn_rank_runs_export.clicked.connect(self.export_ranking_runs)
        view_row.addWidget(self.btn_rank_runs_export)
        self.btn_rank_runs_clear = QPushButton("清空历史")
        self.btn_rank_runs_clear.setToolTip("只清流水记录，不动已抓取的榜单数据")
        self.btn_rank_runs_clear.setProperty("class", "danger")
        self.btn_rank_runs_clear.clicked.connect(self.clear_ranking_runs)
        view_row.addWidget(self.btn_rank_runs_clear)

        tl.addLayout(view_row)

        self.rank_stack = QStackedWidget()
        self.rank_table = QTableWidget(0, 8)
        self.rank_table.setHorizontalHeaderLabels(
            ["名次", "名次变化", "标题", "作者", "字数", "收藏", "日增", "在榜"])
        self.rank_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.rank_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.rank_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.rank_table.setAlternatingRowColors(True)
        self.rank_table.verticalHeader().setVisible(False)
        self.rank_table.verticalHeader().setDefaultSectionSize(36)
        self.rank_table.setShowGrid(False)
        header = self.rank_table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for i, w in enumerate([60, 80, 0, 150, 80, 80, 70, 60]):
            if w:
                self.rank_table.setColumnWidth(i, w)
        self.rank_table.cellDoubleClicked.connect(self.open_rank_work_page)
        # 开启排序后 Qt 默认按第 0 列「降序」，榜单会变成 100 在最上——必须显式指定升序
        self.rank_table.setSortingEnabled(True)
        self.rank_table.sortByColumn(0, Qt.AscendingOrder)
        self.rank_stack.addWidget(self.rank_table)

        self.rank_author_table = QTableWidget(0, 7)
        self.rank_author_table.setHorizontalHeaderLabels(
            ["作者", "作者 ID", "上榜作品", "上榜次数", "最高名次", "最近上榜", "名单状态"])
        self.rank_author_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.rank_author_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.rank_author_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.rank_author_table.setAlternatingRowColors(True)
        self.rank_author_table.verticalHeader().setVisible(False)
        self.rank_author_table.verticalHeader().setDefaultSectionSize(36)
        self.rank_author_table.setShowGrid(False)
        ah = self.rank_author_table.horizontalHeader()
        ah.setSectionResizeMode(0, QHeaderView.Stretch)
        for i, w in enumerate([0, 110, 90, 90, 90, 110, 110]):
            if w:
                self.rank_author_table.setColumnWidth(i, w)
        self.rank_author_table.cellDoubleClicked.connect(
            lambda _r, _c: self.open_rank_author_page())
        # 作者表默认按「上榜次数」降序，跟查询意图一致
        self.rank_author_table.setSortingEnabled(True)
        self.rank_author_table.sortByColumn(3, Qt.DescendingOrder)
        self.rank_stack.addWidget(self.rank_author_table)

        self.rank_run_table = QTableWidget(0, 12)
        self.rank_run_table.setHorizontalHeaderLabels(
            ["开始时间", "来源", "榜单", "榜单日期", "天数", "有数据", "收录",
             "新下载", "跳过", "失败", "过滤", "耗时/结果"])
        self.rank_run_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.rank_run_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.rank_run_table.setAlternatingRowColors(True)
        self.rank_run_table.verticalHeader().setVisible(False)
        self.rank_run_table.verticalHeader().setDefaultSectionSize(36)
        self.rank_run_table.setShowGrid(False)
        rh = self.rank_run_table.horizontalHeader()
        rh.setSectionResizeMode(11, QHeaderView.Stretch)
        for i, w in enumerate([150, 70, 130, 100, 60, 70, 70, 80, 70, 70, 70, 0]):
            if w:
                self.rank_run_table.setColumnWidth(i, w)
        # 表头可点击排序；默认按「开始时间」倒序（最新的在最上）。
        # 时间格式是 YYYY-MM-DD HH:MM:SS，文本比较即等于时间比较。
        self.rank_run_table.setSortingEnabled(True)
        self.rank_run_table.sortByColumn(0, Qt.DescendingOrder)
        self.rank_stack.addWidget(self.rank_run_table)

        tl.addWidget(self.rank_stack, 1)
        outer.addWidget(table_card, 1)

        hint = QLabel("双击打开作品页；「在榜」为连续上榜天数。正文下载复用单篇抓取管线，"
                      "默认只下未收录的作品，霸榜作品不会重复下载。")
        hint.setProperty("class", "hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        self._on_rank_view_changed()
        self._setup_rank_log()
        return page

    def _rank_author_of(self, work_id: str) -> str:
        """取某部作品在排行榜库中记录的作者 ID。"""
        if not hasattr(self, "rank_db") or self.rank_db is None:
            return ""
        try:
            row = self.rank_db._conn.execute(
                "SELECT author_id FROM rank_works WHERE work_id=?", (work_id,)).fetchone()
        except Exception:
            return ""
        return str(row["author_id"] or "") if row else ""

    # ---------------------------------------------------------------- 数据
    def _on_rank_mode_changed(self, _index=0):
        """切换榜单时，把「已有数据的日期」在日历上标出来。"""
        if not hasattr(self, "rank_db") or self.rank_db is None:
            return
        mode = self.rank_mode_combo.currentData()
        cal = self.rank_date_edit.calendarWidget()
        for d in getattr(self, "_rank_marked_dates", set()):
            qd = QDate.fromString(d, "yyyy-MM-dd")
            if qd.isValid():
                cal.setDateTextFormat(qd, QTextCharFormat())
        marked = QTextCharFormat()
        marked.setFontWeight(75)
        try:
            marked.setForeground(QColor(tokens(current_mode())["accent_text"]))
        except Exception:
            pass
        dates = self.rank_db.dates(mode, limit=400)
        for d in dates:
            qd = QDate.fromString(d, "yyyy-MM-dd")
            if qd.isValid():
                cal.setDateTextFormat(qd, marked)
        self._rank_marked_dates = set(dates)
        self.refresh_ranking_table()

    def _current_rank_date(self) -> str:
        return self.rank_date_edit.date().toString("yyyy-MM-dd")

    def refresh_ranking_table(self):
        """刷新作品榜，并同步刷新榜单作者视图。"""
        self._refresh_ranking_works()
        self.refresh_ranking_authors()

    def _refresh_ranking_works(self):
        if not hasattr(self, "rank_db") or self.rank_db is None:
            return
        mode = self.rank_mode_combo.currentData()
        date = self._current_rank_date()
        if not mode or not date:
            self.rank_table.setRowCount(0)
            stats = self.rank_db.snapshot_stats()
            self._rank_say(
                f"已存榜单快照：{sum(v['snapshots'] for v in stats.values())} 次，"
                f"上榜记录 {self.rank_db.total_entries()} 条。"
                if stats else "尚未抓取过榜单。点「抓取榜单」开始。")
            return

        rows = self.rank_db.entries(mode, date)
        if not rows:
            self.rank_table.setRowCount(0)
            known = self.rank_db.dates(mode, limit=400)
            if known:
                self._rank_say(
                    f"{mode_label(mode)}榜 {date} 没有数据。已有数据的日期："
                    f"{known[0]} 等 {len(known)} 天（日历上已标出）。"
                    f"想补抓这一天，直接点「抓取榜单」。")
            else:
                self._rank_say(
                    f"{mode_label(mode)}榜还没有任何数据。点「抓取榜单」抓取当前日期。")
            return
        self.rank_table.setSortingEnabled(False)   # 填充期间禁用排序，避免行错位
        self.rank_table.setRowCount(len(rows))
        tk = tokens(current_mode())
        for i, r in enumerate(rows):
            self.rank_table.setItem(i, 0, _SortItem(str(r["rank"]), r["rank"]))
            self.rank_table.setItem(i, 1, self._rank_delta_item(r.get("rank_delta")))
            self.rank_table.setItem(i, 2, QTableWidgetItem(
                ("[R-18] " if r.get("x_restrict") else "") + (r.get("title") or "-")))
            self.rank_table.setItem(i, 3, QTableWidgetItem(r.get("author_name") or "-"))
            self.rank_table.setItem(i, 4, _SortItem(f"{r.get('word_count') or 0}",
                                                    r.get("word_count") or 0))
            self.rank_table.setItem(i, 5, _SortItem(f"{r.get('bookmark_count') or 0}",
                                                    r.get("bookmark_count") or 0))
            self.rank_table.setItem(i, 6, self._rank_delta_item(
                r.get("bookmark_delta"), suffix="", plus=True))
            self.rank_table.setItem(i, 7, _SortItem(f"{r.get('days_on_board') or 0} 天",
                                                    r.get("days_on_board") or 0))
            item = self.rank_table.item(i, 2)
            item.setData(Qt.UserRole, r.get("work_id"))
            item.setToolTip(f"作品 ID {r.get('work_id')}　标签：{'、'.join(r.get('tags') or []) or '无'}")
        self.rank_table.setSortingEnabled(True)    # 表头可点击排序
        stats = self.rank_db.snapshot_stats()
        self._rank_say(
            f"{mode_label(mode)}榜 {date}：{len(rows)} 条；"
            f"累计快照 {sum(v['snapshots'] for v in stats.values())} 次、"
            f"上榜记录 {self.rank_db.total_entries()} 条。")

    # ---------------------------------------------------------------- 榜单作者
    def _on_rank_view_changed(self, _index=0):
        view = self.rank_view_combo.currentData()
        self.rank_stack.setCurrentIndex({"works": 0, "authors": 1, "runs": 2}.get(view, 0))
        # 只显示当前视图用得到的按钮，别让几排操作挤在一起
        for w in (self.btn_rank_download, self.btn_rank_add_author,
                  self.btn_rank_export, self.btn_rank_history):
            w.setVisible(view == "works")
        for w in (self.rank_only_new_author, self.btn_rank_add_authors,
                  self.btn_rank_open_author):
            w.setVisible(view == "authors")
        for w in (self.btn_rank_runs_export, self.btn_rank_runs_clear):
            w.setVisible(view == "runs")
        if view == "authors":
            self.refresh_ranking_authors()
        elif view == "runs":
            self.refresh_ranking_runs()

    def _rank_say(self, text: str):
        """改写顶部摘要；非「作品榜」视图时让位给该视图自己的摘要。"""
        if getattr(self, "rank_view_combo", None) and \
                self.rank_view_combo.currentData() == "works":
            self.rank_summary.setText(text)

    def refresh_ranking_authors(self):
        """列出从榜单抓到过的作者，并标注是否已加入每日自动抓取名单。"""
        if not hasattr(self, "rank_db") or self.rank_db is None:
            return
        try:
            rows = self.rank_db.authors()
        except Exception:
            return
        only_new = self.rank_only_new_author.isChecked()
        out = []
        for r in rows:
            aid = str(r.get("author_id") or "").strip()
            if not aid:
                continue
            try:
                in_list = self.db.get_author(aid, kind="novel") is not None
            except Exception:
                in_list = False
            if only_new and in_list:
                continue
            out.append((r, aid, in_list))

        t = self.rank_author_table
        t.setSortingEnabled(False)
        t.setRowCount(len(out))
        for i, (r, aid, in_list) in enumerate(out):
            t.setItem(i, 0, QTableWidgetItem(str(r.get("author_name") or "-")))
            id_item = _SortItem(aid, None)
            id_item.setData(Qt.UserRole, aid)
            t.setItem(i, 1, id_item)
            t.setItem(i, 2, _SortItem(str(r.get("works") or 0), r.get("works") or 0))
            t.setItem(i, 3, _SortItem(str(r.get("entries") or 0), r.get("entries") or 0))
            t.setItem(i, 4, _SortItem(str(r.get("best_rank") or "-"),
                                      r.get("best_rank") if r.get("best_rank") else None))
            t.setItem(i, 5, QTableWidgetItem(str(r.get("last_date") or "-")))
            status = QTableWidgetItem("已在名单" if in_list else "未加入")
            try:
                status.setForeground(QColor(tokens(current_mode())[
                    "success" if in_list else "warn"]))
            except Exception:
                pass
            t.setItem(i, 6, status)
        t.setSortingEnabled(True)

        total = len([1 for r in rows if str(r.get("author_id") or "").strip()])
        pending = sum(1 for _r, _a, inl in out if not in_list)
        if getattr(self, "rank_view_combo", None) and \
                self.rank_view_combo.currentData() == "authors":
            self.rank_summary.setText(
                f"榜单共出现 {total} 位作者，其中 {pending} 位还没加入抓取名单。")

    # ---------------------------------------------------------------- 抓取历史
    def refresh_ranking_runs(self):
        """列出每一轮抓取的结果——「某天抓了多少」的唯一持久化来源。"""
        if not hasattr(self, "rank_db") or self.rank_db is None:
            return
        try:
            rows = self.rank_db.runs()
            totals = self.rank_db.run_totals()
        except Exception:
            return
        t = self.rank_run_table
        t.setSortingEnabled(False)     # 填充期间禁用排序，避免行错位
        t.setRowCount(len(rows))
        tk = tokens(current_mode())
        for i, r in enumerate(rows):
            modes = "、".join(
                mode_label(x) for x in str(r.get("modes") or "").split(",") if x) or "-"
            span = str(r.get("rank_date") or "-")
            if int(r.get("days") or 0) > 1 and r.get("date_from"):
                span = f"{r['date_from']} ~ {span}"
            t.setItem(i, 0, QTableWidgetItem(str(r.get("started_at") or "-")))
            t.setItem(i, 1, QTableWidgetItem(str(r.get("source") or "-")))
            t.setItem(i, 2, QTableWidgetItem(modes))
            date_item = QTableWidgetItem(span)
            if int(r.get("days") or 0) > 1:
                date_item.setToolTip(f"补抓 {r['days']} 天，其中 {r.get('days_ok')} 天有数据")
            t.setItem(i, 3, date_item)
            t.setItem(i, 4, QTableWidgetItem(str(r.get("days") or 0)))
            t.setItem(i, 5, QTableWidgetItem(str(r.get("days_ok") or 0)))
            t.setItem(i, 6, QTableWidgetItem(str(r.get("collected") or 0)))
            dl = QTableWidgetItem(str(r.get("downloaded") or 0))
            if r.get("downloaded"):
                dl.setForeground(QColor(tk["success"]))
            t.setItem(i, 7, dl)
            t.setItem(i, 8, QTableWidgetItem(str(r.get("skipped") or 0)))
            fail = QTableWidgetItem(str(r.get("failed") or 0))
            if r.get("failed"):
                fail.setForeground(QColor(tk["danger"]))
            t.setItem(i, 9, fail)
            t.setItem(i, 10, QTableWidgetItem(str(r.get("filtered_out") or 0)))
            ok = bool(r.get("ok"))
            res = QTableWidgetItem(
                f"{float(r.get('elapsed') or 0):.0f}s · " + ("成功" if ok else "有异常"))
            try:
                res.setForeground(QColor(tk["success" if ok else "warn"]))
            except Exception:
                pass
            msg = str(r.get("message") or "")
            if msg:
                res.setToolTip(msg[:400])
            t.setItem(i, 11, res)
        t.setSortingEnabled(True)      # 表头可点击排序

        if getattr(self, "rank_view_combo", None) and \
                self.rank_view_combo.currentData() == "runs":
            if not rows:
                self.rank_summary.setText(
                    "还没有抓取历史。执行过一次榜单抓取后，每轮结果都会记在这里（重启软件也在）。")
            else:
                self.rank_summary.setText(
                    f"共 {totals.get('rounds', len(rows))} 轮：累计收录 "
                    f"{totals.get('collected', 0)} 条，新下载 "
                    f"{totals.get('downloaded', 0)} 部，失败 "
                    f"{totals.get('failed', 0)} 部。")

    def export_ranking_runs(self):
        if not hasattr(self, "rank_db") or self.rank_db is None:
            return
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出抓取历史", f"排行榜抓取历史_{time.strftime('%Y%m%d')}.csv",
            "CSV 文件 (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            n = self.rank_db.export_runs_csv(path)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "已导出", f"共 {n} 轮记录。\n{path}")

    def clear_ranking_runs(self):
        if not hasattr(self, "rank_db") or self.rank_db is None:
            return
        if QMessageBox.question(
                self, "清空抓取历史",
                "只删除「抓取历史」的流水记录，已抓到的榜单数据不受影响。\n确定清空？") \
                != QMessageBox.Yes:
            return
        self.rank_db.clear_runs()
        self.refresh_ranking_runs()

    def _selected_rank_author_ids(self):
        out = []
        t = self.rank_author_table
        for row in sorted({i.row() for i in t.selectedIndexes()}):
            item = t.item(row, 1)
            if item:
                aid = item.data(Qt.UserRole) or item.text()
                if str(aid).strip():
                    out.append(str(aid).strip())
        return out

    def add_selected_rank_authors(self):
        aids = self._selected_rank_author_ids()
        if not aids:
            QMessageBox.information(self, "提示", "请先在列表里选中作者。")
            return
        added = 0
        for aid in aids:
            if not aid.isdigit():
                continue
            name = ""
            try:
                r = self.rank_db._conn.execute(
                    "SELECT author_name FROM rank_works WHERE author_id=? LIMIT 1",
                    (aid,)).fetchone()
                if r:
                    name = r["author_name"] or ""
            except Exception:
                pass
            if self.db.add_author(aid, name, pending_validate=False, kind="novel"):
                added += 1
        QMessageBox.information(
            self, "已加入名单",
            f"新增 {added} 位作者到小说名单（已在名单的不会重复添加）。")
        if added:
            self.refresh_authors()
            self.refresh_ranking_authors()

    def open_rank_author_page(self):
        aids = self._selected_rank_author_ids()
        if not aids:
            QMessageBox.information(self, "提示", "请先在列表里选中作者。")
            return
        for aid in aids[:1]:
            self._open_pixiv_user(aid)

    def _rank_delta_item(self, delta, suffix="", plus=False):
        """名次/收藏变化单元格：上升绿色、下降红色、无变化灰。"""
        text = "-"
        color = "text_muted"
        if delta is not None and delta != 0:
            arrow = "↑" if delta > 0 else "↓"
            text = f"{arrow}{abs(delta)}{suffix}"
            color = "success" if delta > 0 else "danger"
        elif delta == 0:
            text = "0" if not plus else "±0"
        item = QTableWidgetItem(text)
        try:
            item.setForeground(QColor(tokens(current_mode())[color]))
        except Exception:
            pass
        return item

    # ---------------------------------------------------------------- 操作
    def _rank_selected_work_ids(self):
        out = []
        for row in sorted({i.row() for i in self.rank_table.selectedIndexes()}):
            item = self.rank_table.item(row, 2)
            if item:
                wid = item.data(Qt.UserRole)
                if wid:
                    out.append(str(wid))
        return out

    def open_rank_work_page(self, row, _col):
        item = self.rank_table.item(row, 2)
        if not item:
            return
        wid = item.data(Qt.UserRole)
        if wid:
            webbrowser.open(f"https://www.pixiv.net/novel/show.php?id={wid}")

    def add_rank_author(self):
        """把选中作品对应的作者加入小说抓取名单。"""
        work_ids = self._rank_selected_work_ids()
        if not work_ids:
            QMessageBox.information(self, "提示", "请先在列表里选中作品。")
            return
        if not hasattr(self, "rank_db") or self.rank_db is None:
            return
        added = 0
        for wid in work_ids:
            aid = self._rank_author_of(wid)
            if not aid.isdigit():
                continue
            name = ""
            try:
                row = self.rank_db._conn.execute(
                    "SELECT author_name FROM rank_works WHERE work_id=?", (wid,)).fetchone()
                if row:
                    name = row["author_name"] or ""
            except Exception:
                pass
            if self.db.add_author(aid, name, pending_validate=False, kind="novel"):
                added += 1
        QMessageBox.information(self, "已加入名单",
                                f"新增 {added} 位作者到小说名单（重复的不计入）。")
        if added:
            self.refresh_authors()

    def download_selected_rank_work(self):
        """只下载当前选中的作品（不受「仅首次上榜」限制）。"""
        work_ids = self._rank_selected_work_ids()
        if not work_ids:
            QMessageBox.information(self, "提示", "请先在列表里选中作品。")
            return
        self._start_ranking_download(work_ids)

    def export_ranking(self):
        """导出当前榜单为 CSV / JSON。"""
        if not hasattr(self, "rank_db") or self.rank_db is None:
            return
        mode = self.rank_mode_combo.currentData()
        date = self._current_rank_date()
        if not mode or not date:
            QMessageBox.information(self, "提示", "还没有可导出的榜单，请先抓取。")
            return
        default_name = f"排行榜_{mode}_{date}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出榜单", default_name,
            "CSV 文件（Excel 可直接打开） (*.csv);;JSON 文件 (*.json)")
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                n = self.rank_db.export_json(mode, date, path)
            else:
                n = self.rank_db.export_csv(mode, date, path)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出完成", f"已导出 {n} 条：\n{path}")

    def show_rank_history(self):
        """查看所选作品历次上榜记录。"""
        if not hasattr(self, "rank_db") or self.rank_db is None:
            return
        work_ids = self._rank_selected_work_ids()
        if not work_ids:
            QMessageBox.information(self, "提示", "请先在列表里选中一部作品。")
            return
        work_id = work_ids[0]
        rows = self.rank_db.work_history(work_id)
        if not rows:
            QMessageBox.information(self, "提示", "该作品暂无上榜记录。")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"上榜轨迹 — 作品 {work_id}")
        dlg.resize(460, 420)
        lay = QVBoxLayout(dlg)
        title = ""
        try:
            r = self.rank_db._conn.execute(
                "SELECT title, author_name FROM rank_works WHERE work_id=?",
                (work_id,)).fetchone()
            if r:
                title = f"{r['title']}（{r['author_name']}）"
        except Exception:
            pass
        head = QLabel(title or work_id)
        head.setProperty("class", "section-title")
        head.setWordWrap(True)
        lay.addWidget(head)

        table = QTableWidget(len(rows), 4)
        table.setHorizontalHeaderLabels(["日期", "榜单", "名次", "收藏数"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for i, r in enumerate(rows):
            table.setItem(i, 0, QTableWidgetItem(r["rank_date"]))
            table.setItem(i, 1, QTableWidgetItem(mode_label(r["mode"])))
            table.setItem(i, 2, QTableWidgetItem(str(r["rank"])))
            table.setItem(i, 3, QTableWidgetItem(str(r["bookmark_count"])))
        lay.addWidget(table, 1)

        btn_row = QHBoxLayout()
        btn_csv = QPushButton("导出为 CSV")
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.accept)

        def _export():
            p, _ = QFileDialog.getSaveFileName(
                dlg, "导出轨迹", f"上榜轨迹_{work_id}.csv", "CSV 文件 (*.csv)")
            if not p:
                return
            try:
                n = self.rank_db.export_work_history_csv(work_id, p)
            except Exception as e:
                QMessageBox.warning(dlg, "导出失败", str(e))
                return
            QMessageBox.information(dlg, "导出完成", f"已导出 {n} 条记录：\n{p}")

        btn_csv.clicked.connect(_export)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_csv)
        btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)
        dlg.exec()

    def _start_ranking_download(self, work_ids: list):
        """只下载指定的作品（不受「仅首次上榜」限制，用于手动补下载）。"""
        if self.rank_worker and self.rank_worker.isRunning():
            QMessageBox.information(self, "提示", "榜单任务正在运行，请稍候。")
            return
        pairs = {}
        for wid in work_ids:
            aid = self._rank_author_of(wid)
            if aid:
                pairs[wid] = aid
        if not pairs:
            QMessageBox.information(self, "提示", "未能找到所选作品的作者信息。")
            return
        self.btn_rank_fetch.setEnabled(False)
        self.btn_rank_stop.setEnabled(True)
        self.rank_busy.setVisible(True)
        self.rank_summary.setText(f"正在下载 {len(pairs)} 部作品的正文…")
        self.rank_worker = RankingWorker(
            self.settings, self.db, self.rank_db, self.logger,
            modes=[], download_only=pairs)
        self.rank_worker.progress.connect(self.on_rank_progress)
        self.rank_worker.finished_report.connect(self.on_ranking_finished)
        self.rank_worker.start()

    def _rank_modes_for_fetch(self) -> list:
        modes = list(getattr(self.settings, "ranking_modes", []) or [])
        # R-18 可以细分到具体榜单：勾了哪几个就抓哪几个；没细分则沿用旧的总开关
        r18 = [m for m in (getattr(self.settings, "ranking_r18_modes", []) or [])
               if m in R18_MODES]
        if r18:
            modes += [m for m in r18 if m not in modes]
        elif getattr(self.settings, "ranking_include_r18", False):
            modes += [m for m in R18_MODES if m not in modes]
        return [m for m in modes if m in ALL_MODES] or list(RANK_MODES[:2])

    def backfill_ranking(self):
        """往前连续补抓最近 N 天的榜单。"""
        if not hasattr(self, "rank_db") or self.rank_db is None:
            QMessageBox.warning(self, "提示", "排行榜数据未初始化。")
            return
        if self.rank_worker and self.rank_worker.isRunning():
            return
        # 只补抓「当前下拉框选中的那个榜」，跟抓取榜单的范围保持一致
        sel = self.rank_mode_combo.currentData()
        modes = [sel] if sel in ALL_MODES else self._rank_modes_for_fetch()
        days, ok = QInputDialog.getInt(
            self, "补抓历史",
            f"往前补抓最近多少天的「{mode_label(modes[0])}榜」？\n"
            f"（每天约 {len(modes) * 2} 次请求，可随时中止）",
            30, 1, 365, 1)
        if not ok:
            return
        self.btn_rank_fetch.setEnabled(False)
        self.btn_rank_backfill.setEnabled(False)
        self.btn_rank_stop.setEnabled(True)
        self.rank_busy.setVisible(True)
        self.rank_summary.setText(f"正在补抓最近 {days} 天…")
        self._rank_log_start(f"补抓历史：{mode_label(modes[0])}榜，最近 {days} 天")
        self.rank_worker = RankingWorker(
            self.settings, self.db, self.rank_db, self.logger, modes,
            backfill_days=days, source=SRC_BACKFILL)
        self.rank_worker.progress.connect(self.on_rank_progress)
        self.rank_worker.finished_report.connect(self.on_ranking_finished)
        self.rank_worker.start()

    def fetch_ranking(self, use_all_modes: bool = False, source: str = ""):
        """抓取榜单。

        use_all_modes=False（页面按钮）→ 只抓下拉框里当前选中的那个榜；
        use_all_modes=True（每日定时任务）→ 抓设置里勾选的所有榜单。
        source 会记进抓取历史，用来区分「手动 / 定时」。
        """
        if not hasattr(self, "rank_db") or self.rank_db is None:
            QMessageBox.warning(self, "提示", "排行榜数据未初始化。")
            return
        if self.rank_worker and self.rank_worker.isRunning():
            return
        if use_all_modes:
            modes = self._rank_modes_for_fetch()
            # 定时任务固定抓「今天」，绝不能被日历控件当前停留的历史日期带偏
            date = None
        else:
            sel = self.rank_mode_combo.currentData()
            modes = [sel] if sel in ALL_MODES else self._rank_modes_for_fetch()
            # 手动抓取：选了过去的日期 = 补抓历史；今天则交给接口自己决定
            # （每日榜是前一天的成绩）
            picked = self._current_rank_date()
            date = None if picked == QDate.currentDate().toString("yyyy-MM-dd") \
                else picked.replace("-", "")
        self.btn_rank_fetch.setEnabled(False)
        self.btn_rank_stop.setEnabled(True)
        self.rank_busy.setVisible(True)
        self.rank_summary.setText("正在抓取榜单…")
        self._rank_log_start("抓取榜单")
        self.rank_worker = RankingWorker(
            self.settings, self.db, self.rank_db, self.logger, modes, date=date,
            source=source or SRC_MANUAL)
        self.rank_worker.progress.connect(self.on_rank_progress)
        self.rank_worker.finished_report.connect(self.on_ranking_finished)
        self.rank_worker.start()

    def stop_ranking(self):
        if self.rank_worker and self.rank_worker.isRunning():
            self.rank_worker.stop()
            self.rank_summary.setText("正在中止…")

    def on_rank_progress(self, event: dict):
        tag = event.get("tag")
        if tag == "rank_start":
            self.rank_summary.setText(f"正在抓取 {mode_label(event.get('mode', ''))}榜…")
        elif tag == "rank_backfill":
            self.rank_summary.setText(
                f"正在补抓 {event.get('date', '')}（{event.get('index', 0)}/"
                f"{event.get('total', 0)}）…")
        elif tag == "rank_downloaded":
            self.rank_summary.setText(f"已下载作品 {event.get('work_id')}")

    def on_ranking_finished(self, report: dict):
        self.btn_rank_fetch.setEnabled(True)
        self.btn_rank_backfill.setEnabled(True)
        self.btn_rank_stop.setEnabled(False)
        self.rank_busy.setVisible(False)
        r = report or {}
        if r.get("auth_expired"):
            self.rank_summary.setText("登录已失效，请重新导入 Cookie 后再抓取榜单。")
        elif r.get("errors") and not r.get("collected"):
            self.rank_summary.setText("抓取失败：" + "；".join(r["errors"]))
        else:
            parts = []
            if r.get("days"):
                done = len(r["days"])
                parts.append(f"补抓完成：{done} 天中有 {r.get('days_ok', 0)} 天取到数据，"
                             f"共收录 {r.get('collected', 0)} 条")
                failed_days = [d["date"] for d in r["days"] if not d["collected"]]
                if failed_days:
                    parts.append(f"无数据的日期：{'、'.join(failed_days[:8])}"
                                 + ("…" if len(failed_days) > 8 else ""))
            else:
                parts = [f"抓取完成：收录 {r.get('collected', 0)} 条"]
            if r.get("downloaded"):
                parts.append(f"新下载正文 {r['downloaded']} 部")
            if r.get("skipped"):
                parts.append(f"跳过（已在库）{r['skipped']} 部")
            if r.get("failed"):
                parts.append(f"失败 {r['failed']} 部")
            if r.get("errors"):
                parts.append("提示：" + "；".join(r["errors"]))
            if r.get("filtered_out"):
                parts.append(f"已过滤非简体中文 {r['filtered_out']} 条")
            if r.get("csv"):
                parts.append(f"已生成 {len(r['csv'])} 份 CSV：{r['csv'][0]}")
            self.rank_summary.setText("；".join(parts))

        # 自动跳到刚抓到的日期并刷新列表（每日榜通常是前一天）
        got_date = r.get("rank_date") or ""
        if got_date:
            qd = QDate.fromString(got_date, "yyyy-MM-dd")
            if qd.isValid():
                self.rank_date_edit.setDate(qd)
        self._update_rank_last_label(r)
        # 把本轮结果也写进日志，方便回看
        if r.get("collected") or r.get("errors"):
            self._rank_log_stop(
                f"=== 结束：收录 {r.get('collected', 0)} 条"
                + (f"，新下载 {r.get('downloaded', 0)} 部" if r.get("downloaded") else "")
                + (f"，失败 {r.get('failed', 0)} 部" if r.get("failed") else "")
                + " ===")
        else:
            self._rank_log_stop("=== 结束 ===")
        self._on_rank_mode_changed()
        self.refresh_authors()
        self.refresh_failed()
        self.refresh_ranking_runs()
        if getattr(self, "on_ranking_done", None):
            self.on_ranking_done(r)

    def _update_rank_last_label(self, r: dict):
        """把本轮榜单任务的结果留在页面上（新增/下载/跳过/失败 + 时间）。"""
        r = r or {}
        if not r:
            return
        when = time.strftime("%m-%d %H:%M")
        if r.get("auth_expired"):
            text = f"上次任务 {when}：登录已失效，请重新导入 Cookie"
            cls = "status-err"
        elif r.get("errors") and not r.get("collected"):
            text = f"上次任务 {when}：未取到数据 — {r['errors'][0]}"
            cls = "status-err"
        else:
            parts = [f"上次任务 {when}：收录 {r.get('collected', 0)} 条"]
            if r.get("days"):
                parts.append(f"补抓 {len(r['days'])} 天（{r.get('days_ok', 0)} 天有数据）")
            if r.get("downloaded"):
                parts.append(f"新下载小说 {r.get('downloaded')} 部")
            if r.get("skipped"):
                parts.append(f"跳过 {r.get('skipped')} 部")
            if r.get("failed"):
                parts.append(f"失败 {r.get('failed')} 部")
            if r.get("filtered_out"):
                parts.append(f"过滤非简体中文 {r.get('filtered_out')} 条")
            if r.get("csv"):
                parts.append(f"CSV {len(r['csv'])} 份")
            text = "，".join(parts)
            cls = "status-err" if r.get("failed") else "status-ok"
        self.rank_last_label.setText(text)
        try:
            self.rank_last_label.setProperty("class", cls)
            self.style().polish(self.rank_last_label)
        except Exception:
            pass

    # ---------------------------------------------------------------- 日志
    def _setup_rank_log(self):
        """初始化排行榜日志：队列 + 定时器（跨线程安全）+ 处理器。"""
        self._rank_log_queue = queue.Queue()
        self._rank_log_timer = QTimer(self)
        self._rank_log_timer.setInterval(200)
        self._rank_log_timer.timeout.connect(self._drain_rank_log)
        self._rank_log_timer.start()
        self._rank_log_handler = _RankLogHandler(self._rank_log_queue.put)
        try:
            logging.getLogger("pixiv_scraper").addHandler(self._rank_log_handler)
        except Exception:
            pass

    def _drain_rank_log(self):
        try:
            while True:
                line = self._rank_log_queue.get_nowait()
                self.rank_log_view.appendPlainText(line)
        except queue.Empty:
            pass

    def _rank_log_start(self, title: str):
        """开始记录：清空并打开开关。"""
        if getattr(self, "_rank_log_handler", None):
            self._rank_log_handler.active = True
        self.rank_log_view.appendPlainText(f"=== {title} ===")

    def _rank_log_stop(self, tail: str = ""):
        if tail:
            self.rank_log_view.appendPlainText(tail)
        if getattr(self, "_rank_log_handler", None):
            self._rank_log_handler.active = False

    def clear_rank_log(self):
        self.rank_log_view.clear()
