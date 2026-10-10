import os
import re

from PySide6.QtCore import Qt, QTime
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
    QDoubleSpinBox, QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem,
    QTimeEdit, QVBoxLayout, QWidget,
)

from .. import credentials
from ..autostart import set_autostart
from ..config import save_settings as persist_settings
from ..core import paths
from ..core.models import status_label
from ..pixiv.client import PixivClient
from .icons import app_mark, icon
from .theme import apply_theme, current_mode, resolve_mode, tokens
from .worker import ScrapeWorker

_AUTHOR_ID_RE = re.compile(r"^\d+$")


def _set_class(widget, cls: str):
    """切换自定义样式类并立即刷新样式。"""
    widget.setProperty("class", cls)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class BatchImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量导入作者 ID")
        self.setMinimumSize(460, 300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("批量导入作者 ID")
        title.setProperty("class", "page-title")
        sub = QLabel("每行一个，或用逗号 / 空格 / 分号分隔，只识别纯数字 ID。")
        sub.setProperty("class", "page-sub")
        layout.addWidget(title)
        layout.addWidget(sub)

        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("例如：\n1234567\n8901234, 5678901")
        layout.addWidget(self.edit, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        btn_file = QPushButton("从文件导入…")
        btn_file.setIcon(icon("folder", tokens()["accent_text"]))
        btn_file.clicked.connect(self._from_file)
        btn_ok = QPushButton("确定")
        btn_ok.setProperty("class", "primary")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(btn_file)
        row.addStretch(1)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        layout.addLayout(row)

    def _from_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文本文件", "", "文本文件 (*.txt);;所有文件 (*)")
        if path:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                self.edit.setPlainText(f.read())

    def ids(self) -> list:
        text = self.edit.toPlainText()
        return [s for s in re.split(r"[\s,，、;；]+", text) if _AUTHOR_ID_RE.match(s)]


class MainWindow(QMainWindow):
    def __init__(self, settings, db, logger):
        super().__init__()
        self.settings = settings
        self.db = db
        self.logger = logger
        self.worker = None
        self.on_fetch_done = None
        self.on_theme_changed = None

        self.setWindowTitle("Pixiv 小说抓取器")
        self.resize(1140, 740)
        self.setMinimumSize(940, 620)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        root.addWidget(self._build_topbar())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 12)
        body_layout.setSpacing(12)

        self.stack = QStackedWidget()
        body_layout.addWidget(self._build_sidebar())
        body_layout.addWidget(self.stack, 1)
        root.addWidget(body, 1)

        self.stack.addWidget(self._build_authors_page())
        self.stack.addWidget(self._build_tasks_page())
        self.stack.addWidget(self._build_search_page())
        self.stack.addWidget(self._build_settings_page())

        self.stat_label = QLabel("")
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        sb.addPermanentWidget(self.stat_label)

        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(lambda _i: self._decorate())
        self.settings_nav.currentRowChanged.connect(lambda _i: self._decorate())

        self.refresh_authors()
        self.refresh_failed()
        self._refresh_conn_badge()
        self._decorate()

    # ================================================================ 骨架
    def _build_topbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topbar")
        bar.setFixedHeight(62)
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 0, 18, 0)
        h.setSpacing(12)

        mark = QLabel()
        mark.setPixmap(app_mark(34))
        mark.setFixedSize(34, 34)
        h.addWidget(mark)

        title = QLabel("Pixiv 小说抓取器")
        title.setProperty("class", "page-title")
        title.setStyleSheet("font-size: 15px;")
        subtitle = QLabel("小说 / 插画 本地同步")
        subtitle.setProperty("class", "page-sub")
        tv = QVBoxLayout()
        tv.setSpacing(1)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.addWidget(title)
        tv.addWidget(subtitle)
        h.addLayout(tv)
        h.addStretch(1)

        self.conn_badge = QLabel("")
        self.conn_badge.setProperty("class", "badge-idle")
        h.addWidget(self.conn_badge)

        self.btn_theme = QPushButton()
        self.btn_theme.setProperty("class", "icon")
        self.btn_theme.setToolTip("切换浅色 / 深色主题")
        self.btn_theme.clicked.connect(self._toggle_theme)
        h.addWidget(self.btn_theme)

        btn_hide = QPushButton("隐藏到托盘")
        btn_hide.setProperty("class", "ghost")
        btn_hide.clicked.connect(self.hide)
        h.addWidget(btn_hide)
        return bar

    def _build_sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(186)
        v = QVBoxLayout(side)
        v.setContentsMargins(6, 10, 6, 10)
        v.setSpacing(4)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        for name in ["作者", "任务", "检索", "设置"]:
            self.nav.addItem(name)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        v.addWidget(self.nav, 1)

        tip = QLabel("关闭窗口只会隐藏到系统托盘\n完全退出请在托盘菜单中选择")
        tip.setProperty("class", "hint")
        tip.setWordWrap(True)
        v.addWidget(tip)
        return side

    def _card(self, margin=14, spacing=10):
        frame = QFrame()
        frame.setProperty("class", "card")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(margin, margin, margin, margin)
        lay.setSpacing(spacing)
        return frame, lay

    def _page(self, title: str, subtitle: str):
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(4, 0, 4, 14)
        outer.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        t = QLabel(title)
        t.setProperty("class", "page-title")
        s = QLabel(subtitle)
        s.setProperty("class", "page-sub")
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.addWidget(t)
        tv.addWidget(s)
        head.addLayout(tv)
        head.addStretch(1)
        outer.addLayout(head)
        return w, outer, head

    # ================================================================ 作者
    def _make_author_table(self, headers: list) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        for i, w in enumerate([220, 100, 64, 140, 96, 84, 84][:len(headers)]):
            table.setColumnWidth(i, w)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(38)
        table.setShowGrid(True)
        table.setSortingEnabled(True)
        table.cellDoubleClicked.connect(self._on_author_cell_double_clicked)
        return table

    def _build_authors_page(self):
        page, outer, _head = self._page(
            "作者管理", "维护需要同步的小说 / 插画作者名单，双击昵称可打开 Pixiv 主页")

        bar, bl = self._card(margin=12, spacing=8)
        row = QHBoxLayout()
        row.setSpacing(8)

        self.author_input = QLineEdit()
        self.author_input.setPlaceholderText("输入作者数字 ID，回车快速添加")
        self.author_input.returnPressed.connect(self.add_author)
        self.author_input.setFixedWidth(280)

        btn_add = QPushButton("添加")
        btn_add.setProperty("class", "primary")
        btn_add.clicked.connect(self.add_author)
        btn_batch = QPushButton("批量导入")
        btn_batch.clicked.connect(self.batch_import)

        row.addWidget(self.author_input)
        row.addWidget(btn_add)
        row.addWidget(btn_batch)
        row.addStretch(1)

        btn_open = QPushButton("打开主页")
        btn_open.clicked.connect(self.open_author_page)
        btn_toggle = QPushButton("启用 / 停用")
        btn_toggle.clicked.connect(self.toggle_author)
        btn_del = QPushButton("删除")
        btn_del.setProperty("class", "danger")
        btn_del.clicked.connect(self.delete_author)
        row.addWidget(btn_open)
        row.addWidget(btn_toggle)
        row.addWidget(btn_del)
        bl.addLayout(row)
        outer.addWidget(bar)

        table_card, tl = self._card(margin=12, spacing=8)
        self.author_tabs = QTabWidget()
        self.novel_author_table = self._make_author_table(
            ["昵称", "作者 ID", "状态", "最近抓取", "已收录小说", "已删单篇", "已删系列"])
        self.illust_author_table = self._make_author_table(
            ["昵称", "作者 ID", "状态", "最近抓取", "已收录插画", "已删插画"])
        self.author_tabs.addTab(self.novel_author_table, "小说作者")
        self.author_tabs.addTab(self.illust_author_table, "插画作者")
        tl.addWidget(self.author_tabs, 1)
        outer.addWidget(table_card, 1)
        return page

    def _active_author_kind(self) -> str:
        return "novel" if self.author_tabs.currentIndex() == 0 else "illust"

    def _active_author_table(self) -> QTableWidget:
        return self.novel_author_table if self._active_author_kind() == "novel" else self.illust_author_table

    def _on_author_cell_double_clicked(self, row, col):
        if col != 0:
            return
        table = self.sender()
        aid = table.item(row, 1).text()
        self._open_pixiv_user(aid)

    @staticmethod
    def _open_pixiv_user(aid: str):
        import webbrowser
        webbrowser.open(f"https://www.pixiv.net/users/{aid}")

    def add_author(self):
        text = self.author_input.text().strip()
        if not _AUTHOR_ID_RE.match(text):
            QMessageBox.warning(self, "输入无效", "作者 ID 应为纯数字")
            return
        kind = self._active_author_kind()
        if self.db.add_author(text, pending_validate=True, kind=kind):
            self.author_input.clear()
            self.refresh_authors()
        else:
            label = "小说" if kind == "novel" else "插画"
            QMessageBox.information(self, "已存在", f"该作者已在{label}名单中")

    def batch_import(self):
        dlg = BatchImportDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        ids = dlg.ids()
        kind = self._active_author_kind()
        label = "小说" if kind == "novel" else "插画"
        added = sum(1 for i in ids if self.db.add_author(i, pending_validate=True, kind=kind))
        QMessageBox.information(self, "导入结果",
                                f"导入到{label}名单：共 {len(ids)} 个，成功 {added}，重复/无效 {len(ids) - added}")
        self.refresh_authors()

    def _selected_author_id(self):
        table = self._active_author_table()
        row = table.currentRow()
        if row < 0:
            return None
        return table.item(row, 1).text()

    def open_author_page(self):
        aid = self._selected_author_id()
        if aid:
            self._open_pixiv_user(aid)

    def toggle_author(self):
        aid = self._selected_author_id()
        if not aid:
            return
        kind = self._active_author_kind()
        cur = self.db.get_author(aid, kind=kind)
        if cur:
            self.db.set_author_enabled(aid, not cur["enabled"], kind=kind)
            self.refresh_authors()

    def delete_author(self):
        aid = self._selected_author_id()
        if not aid:
            return
        kind = self._active_author_kind()
        label = "小说" if kind == "novel" else "插画"
        if QMessageBox.question(self, "确认", f"从{label}名单删除作者 {aid}？本地已下载文件不会被删除。") == QMessageBox.Yes:
            self.db.remove_author(aid, kind=kind)
            self.refresh_authors()

    def _fill_author_table(self, table: QTableWidget, kind: str):
        authors = self.db.list_authors(kind=kind)
        accent = tokens(current_mode())["accent"]
        table.setSortingEnabled(False)
        table.setRowCount(len(authors))
        for i, a in enumerate(authors):
            aid = a["author_id"]
            name = a.get("author_name") or ("（待校验）" if a.get("pending_validate") else "（未知）")
            if kind == "novel":
                counts = [
                    self.db.author_novel_count(aid),
                    self.db.author_deleted_novel_count(aid),
                    self.db.deleted_series_count(aid),
                ]
            else:
                counts = [
                    self.db.author_illust_count(aid),
                    self.db.author_deleted_illust_count(aid),
                ]

            name_item = QTableWidgetItem(str(name))
            name_item.setForeground(QColor(accent))
            font = name_item.font()
            font.setUnderline(True)
            name_item.setFont(font)
            name_item.setToolTip("双击打开 Pixiv 主页")
            table.setItem(i, 0, name_item)

            enabled = a["enabled"]
            state_item = QTableWidgetItem("启用" if enabled else "停用")
            state_item.setForeground(QColor(tokens(current_mode())["success" if enabled else "text_muted"]))
            table.setItem(i, 2, state_item)

            table.setItem(i, 1, QTableWidgetItem(str(aid)))
            table.setItem(i, 3, QTableWidgetItem(str(a.get("last_fetch_at") or "-")))

            for offset, val in enumerate(counts):
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, val)
                table.setItem(i, 4 + offset, item)
        table.setSortingEnabled(True)

    def refresh_authors(self):
        self._fill_author_table(self.novel_author_table, "novel")
        self._fill_author_table(self.illust_author_table, "illust")
        try:
            n = len(self.db.list_authors(kind="novel"))
            m = len(self.db.list_authors(kind="illust"))
            root = self.settings.epub_root or "未设置"
            self.stat_label.setText(f"小说作者 {n} 位  ·  插画作者 {m} 位  ·  EPUB：{root}")
        except Exception:
            pass

    # ================================================================ 任务
    def _build_tasks_page(self):
        page, outer, _head = self._page("抓取任务", "手动触发一次完整同步，或查看失败 / 远端已删除的作品")

        bar, bl = self._card(margin=12, spacing=8)
        row = QHBoxLayout()
        row.setSpacing(8)

        self.btn_run = QPushButton("立即抓取")
        self.btn_run.setProperty("class", "primary")
        self.btn_run.clicked.connect(self.start_fetch)
        self.btn_stop = QPushButton("中止任务")
        self.btn_stop.setProperty("class", "danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_fetch)

        self.run_status = QLabel("就绪")
        self.run_status.setProperty("class", "badge-idle")

        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setFixedWidth(110)
        self.busy.setTextVisible(False)
        self.busy.setVisible(False)

        btn_logs = QPushButton("打开日志目录")
        btn_logs.clicked.connect(self.open_log_dir)

        row.addWidget(self.btn_run)
        row.addWidget(self.btn_stop)
        row.addSpacing(4)
        row.addWidget(self.run_status)
        row.addWidget(self.busy)
        row.addStretch(1)
        row.addWidget(btn_logs)
        bl.addLayout(row)
        outer.addWidget(bar)

        log_card, lc = self._card(margin=12, spacing=8)
        head = QHBoxLayout()
        t = QLabel("运行日志")
        t.setProperty("class", "section-title")
        head.addWidget(t)
        head.addStretch(1)
        btn_clear_log = QPushButton("清空")
        btn_clear_log.setProperty("class", "ghost")
        btn_clear_log.clicked.connect(self.progress_view_clear)
        head.addWidget(btn_clear_log)
        lc.addLayout(head)

        self.progress_view = QPlainTextEdit()
        self.progress_view.setProperty("class", "log")
        self.progress_view.setReadOnly(True)
        self.progress_view.setMaximumBlockCount(2000)
        self.progress_view.setPlaceholderText("点击「立即抓取」开始，运行过程会实时显示在这里。")
        lc.addWidget(self.progress_view, 1)
        outer.addWidget(log_card, 2)

        fail_card, fc = self._card(margin=12, spacing=8)
        fhead = QHBoxLayout()
        ft = QLabel("失败 / 远端已删除")
        ft.setProperty("class", "section-title")
        self.failed_count = QLabel("")
        self.failed_count.setProperty("class", "hint")
        fhead.addWidget(ft)
        fhead.addWidget(self.failed_count)
        fhead.addStretch(1)
        fc.addLayout(fhead)

        self.failed_table = QTableWidget(0, 4)
        self.failed_table.setHorizontalHeaderLabels(["作品 ID", "标题", "说明", "状态"])
        self.failed_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.failed_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.failed_table.setAlternatingRowColors(True)
        self.failed_table.verticalHeader().setVisible(False)
        self.failed_table.verticalHeader().setDefaultSectionSize(34)
        self.failed_table.setShowGrid(False)
        fc.addWidget(self.failed_table, 1)
        outer.addWidget(fail_card, 1)
        return page

    def progress_view_clear(self):
        self.progress_view.clear()

    def open_log_dir(self):
        from ..logging_setup import log_dir
        os.startfile(log_dir())

    def start_fetch(self):
        if self.worker and self.worker.isRunning():
            return
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.busy.setVisible(True)
        _set_class(self.run_status, "badge")
        self.run_status.setText("抓取中…")
        self.progress_view.appendPlainText("=== 任务开始 ===")
        self.worker = ScrapeWorker(self.settings, self.db, self.logger, self)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_report.connect(self.on_finished)
        self.worker.start()

    def stop_fetch(self):
        if not (self.worker and self.worker.isRunning()):
            return
        if QMessageBox.question(self, "确认中止",
                                "确定要中止当前抓取任务？\n当前作品会处理完再停止，已下载的内容不会丢失。") != QMessageBox.Yes:
            return
        self.worker.stop()
        self.btn_stop.setEnabled(False)
        _set_class(self.run_status, "badge-warn")
        self.run_status.setText("正在中止…")
        self.progress_view.appendPlainText("※ 收到中止请求，等待当前作品处理完成…")

    def on_progress(self, event: dict):
        kind = event.get("kind")
        if kind == "author_start":
            self.progress_view.appendPlainText(f"→ 处理作者 {event.get('author_name')}（{event.get('author_id')}）")
        elif kind == "novel_done":
            tag = "重打包" if event.get("updated") else "新下载"
            self.progress_view.appendPlainText(f"  [{tag}] {event.get('title')}（{event.get('novel_id')}）")
        elif kind == "illust_done":
            tag = "图片更新" if event.get("updated") else "图片下载"
            self.progress_view.appendPlainText(f"  [{tag}] {event.get('title')}（{event.get('illust_id')}）")
        elif kind == "author_done":
            line = (
                f"  完成：新增 {event.get('new')} 更新 {event.get('updated')} 跳过 {event.get('skipped')} 失败 {event.get('failed')}"
            )
            if event.get("deleted"):
                line += f" 删除 {event.get('deleted')}"
            if event.get("illust_new") or event.get("illust_updated") or event.get("illust_failed") or event.get("illust_deleted"):
                line += (
                    f"；图片 新增 {event.get('illust_new')} 更新 {event.get('illust_updated')}"
                    f" 删除 {event.get('illust_deleted')} 失败 {event.get('illust_failed')}"
                )
            self.progress_view.appendPlainText(line)

    def on_finished(self, report):
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.busy.setVisible(False)
        failed = getattr(report, "failed_count", 0)
        if getattr(report, "aborted_reason", None):
            _set_class(self.run_status, "badge-warn")
            self.run_status.setText("已中止")
        elif failed:
            _set_class(self.run_status, "badge-err")
            self.run_status.setText(f"完成 · {failed} 项失败")
        else:
            _set_class(self.run_status, "badge-ok")
            self.run_status.setText("已完成")

        summary = (
            f"=== 任务结束：小说 新增 {report.new_count}，更新 {report.updated_count}，"
            f"跳过 {report.skipped_count}，失败 {report.failed_count}，远端删除 {report.deleted_count}"
        )
        if report.illust_new or report.illust_updated or report.illust_failed or report.illust_deleted:
            summary += (
                f"；图片 新增 {report.illust_new}，更新 {report.illust_updated}，"
                f"失败 {report.illust_failed}，远端删除 {report.illust_deleted}"
            )
        summary += " ==="
        if report.aborted_reason:
            summary += f"\n{report.aborted_reason}"
        self.progress_view.appendPlainText(summary)
        self.refresh_authors()
        self.refresh_failed()
        if self.on_fetch_done:
            self.on_fetch_done(report)

    def refresh_failed(self):
        rows = [
            (n["novel_id"], n.get("title") or "-", n.get("error_reason") or "-", status_label(n["status"]))
            for n in self.db.failed_novels()
        ]
        rows += [
            (i["illust_id"], "[图] " + (i.get("title") or "-"), i.get("error_reason") or "-", status_label(i["status"]))
            for i in self.db.failed_illusts()
        ]
        rows += [
            (n["novel_id"], n.get("title") or "-", "远端已删除（本地文件保留）", status_label(n["status"]))
            for n in self.db.deleted_novels()
        ]
        rows += [
            (i["illust_id"], "[图] " + (i.get("title") or "-"), "远端已删除（本地文件保留）", status_label(i["status"]))
            for i in self.db.deleted_illusts()
        ]
        self.failed_table.setSortingEnabled(False)
        self.failed_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for c, text in enumerate(row):
                self.failed_table.setItem(i, c, QTableWidgetItem(str(text)))
        self.failed_count.setText(f"共 {len(rows)} 条" if rows else "暂无记录")

    # ================================================================ 检索
    def _build_search_page(self):
        page, outer, _head = self._page("检索", "按标题 / 作者昵称 / 标签在本地库中查找已抓取的作品")

        bar, bl = self._card(margin=12, spacing=8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setProperty("class", "search")
        self.search_input.setPlaceholderText("按标题 / 作者昵称 / 标签搜索，回车开始")
        self.search_input.returnPressed.connect(self.do_search)
        btn = QPushButton("搜索")
        btn.setProperty("class", "primary")
        btn.clicked.connect(self.do_search)
        row.addWidget(self.search_input, 1)
        row.addWidget(btn)
        bl.addLayout(row)
        outer.addWidget(bar)

        res_card, rc = self._card(margin=12, spacing=8)
        rhead = QHBoxLayout()
        rt = QLabel("搜索结果")
        rt.setProperty("class", "section-title")
        self.search_count = QLabel("")
        self.search_count.setProperty("class", "hint")
        rhead.addWidget(rt)
        rhead.addWidget(self.search_count)
        rhead.addStretch(1)
        btn_refresh = QPushButton("刷新")
        btn_refresh.setProperty("class", "ghost")
        btn_refresh.clicked.connect(self.do_search)
        rhead.addWidget(btn_refresh)
        rc.addLayout(rhead)

        self.search_table = QTableWidget(0, 4)
        self.search_table.setHorizontalHeaderLabels(["标题", "作者", "状态", "本地路径"])
        self.search_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.search_table.setColumnWidth(1, 150)
        self.search_table.setColumnWidth(2, 90)
        self.search_table.setColumnWidth(3, 280)
        self.search_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.search_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.search_table.setAlternatingRowColors(True)
        self.search_table.verticalHeader().setVisible(False)
        self.search_table.verticalHeader().setDefaultSectionSize(36)
        self.search_table.setShowGrid(False)
        self.search_table.doubleClicked.connect(self.open_novel_dir)
        rc.addWidget(self.search_table, 1)
        outer.addWidget(res_card, 1)

        hint = QLabel("双击任意一行可打开该作品所在的目录")
        hint.setProperty("class", "hint")
        outer.addWidget(hint)
        return page

    def do_search(self):
        kw = self.search_input.text().strip()
        if not kw:
            return
        results = self.db.search_novels(kw)
        self.search_table.setRowCount(len(results))
        for i, n in enumerate(results):
            for c, text in enumerate([
                n.get("title") or "-", n.get("author_name") or "-",
                status_label(n["status"]), n.get("local_path") or "-",
            ]):
                self.search_table.setItem(i, c, QTableWidgetItem(str(text)))
        self.search_count.setText(f"共 {len(results)} 条")

    def open_novel_dir(self):
        row = self.search_table.currentRow()
        if row < 0:
            return
        path = self.search_table.item(row, 3).text()
        if path and os.path.exists(path):
            os.startfile(os.path.dirname(path))

    # ================================================================ 设置
    def _build_settings_page(self):
        page, outer, head = self._page("设置", "存储位置、抓取策略、网络与登录凭据")

        btn_save = QPushButton("保存设置")
        btn_save.setProperty("class", "primary")
        btn_save.clicked.connect(self.save_settings)
        head.addWidget(btn_save)

        body = QHBoxLayout()
        body.setSpacing(12)
        outer.addLayout(body, 1)

        nav_card, nc = self._card(margin=8, spacing=4)
        nav_card.setFixedWidth(172)
        self.settings_nav = QListWidget()
        self.settings_nav.setObjectName("subnav")
        self.settings_nav.addItems(["外观", "存储路径", "抓取策略", "网络", "登录 Cookie", "系统"])
        nc.addWidget(self.settings_nav)
        body.addWidget(nav_card)

        self.settings_stack = QStackedWidget()
        body.addWidget(self.settings_stack, 1)
        self.settings_nav.currentRowChanged.connect(self.settings_stack.setCurrentIndex)

        def make_page() -> QFormLayout:
            w = QWidget()
            outer_l = QVBoxLayout(w)
            outer_l.setContentsMargins(0, 0, 0, 0)
            outer_l.setSpacing(0)
            card, cl = self._card(margin=16, spacing=12)
            f = QFormLayout()
            f.setSpacing(12)
            f.setHorizontalSpacing(16)
            f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cl.addLayout(f)
            outer_l.addWidget(card)
            outer_l.addStretch(1)
            self.settings_stack.addWidget(w)
            return f

        s = self.settings

        # ---- 外观 ----
        form = make_page()
        self.theme_combo = QComboBox()
        for label, value in [("浅色", "light"), ("深色", "dark"), ("跟随系统", "auto")]:
            self.theme_combo.addItem(label, value)
        idx = self.theme_combo.findData(getattr(s, "theme", "light"))
        self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        form.addRow("主题", self.theme_combo)

        appearance_hint = QLabel(
            "切换立即生效并自动保存；也可以点击右上角的月亮 / 太阳图标快速切换。")
        appearance_hint.setProperty("class", "hint")
        form.addRow("", appearance_hint)

        # ---- 存储路径 ----
        form = make_page()
        self.set_epub = QLineEdit(s.epub_root)
        btn_epub = QPushButton("浏览…")
        btn_epub.clicked.connect(lambda: self._pick_dir(self.set_epub))
        r = QHBoxLayout(); r.setSpacing(8); r.addWidget(self.set_epub, 1); r.addWidget(btn_epub)
        form.addRow("EPUB 目录", r)

        self.set_md = QLineEdit(s.md_root)
        self.set_md.setPlaceholderText("留空 = 与 EPUB 同目录")
        btn_md = QPushButton("浏览…")
        btn_md.clicked.connect(lambda: self._pick_dir(self.set_md))
        r = QHBoxLayout(); r.setSpacing(8); r.addWidget(self.set_md, 1); r.addWidget(btn_md)
        form.addRow("小说 MD 目录", r)

        self.set_illust_md = QLineEdit(getattr(s, "illust_md_root", "") or "")
        self.set_illust_md.setPlaceholderText("留空 = 与小说 MD 同目录")
        btn_imd = QPushButton("浏览…")
        btn_imd.clicked.connect(lambda: self._pick_dir(self.set_illust_md))
        r = QHBoxLayout(); r.setSpacing(8); r.addWidget(self.set_illust_md, 1); r.addWidget(btn_imd)
        form.addRow("插画 MD 目录", r)

        self.set_illust_root = QLineEdit(getattr(s, "illust_root", "") or "")
        self.set_illust_root.setPlaceholderText("留空 = EPUB 目录下的「图片」文件夹")
        btn_illust = QPushButton("浏览…")
        btn_illust.clicked.connect(lambda: self._pick_dir(self.set_illust_root))
        r = QHBoxLayout(); r.setSpacing(8); r.addWidget(self.set_illust_root, 1); r.addWidget(btn_illust)
        form.addRow("图片保存目录", r)

        self.set_pattern = QComboBox()
        for label, pattern in [
            ("标题.作品ID（默认）", "{title}.{id}"),
            ("仅标题", "{title}"),
            ("作者_标题", "{author}_{title}"),
            ("标题_作者", "{title}_{author}"),
        ]:
            self.set_pattern.addItem(label, pattern)
        idx = self.set_pattern.findData(s.name_pattern)
        self.set_pattern.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("文件命名规则", self.set_pattern)

        # ---- 抓取策略 ----
        form = make_page()
        self.set_time = QTimeEdit(QTime.fromString(s.fetch_time, "HH:mm"))
        self.set_time.setDisplayFormat("HH:mm")
        form.addRow("每日抓取时间", self.set_time)

        self.set_interval = QDoubleSpinBox()
        self.set_interval.setRange(0.5, 60.0)
        self.set_interval.setValue(float(s.request_interval))
        self.set_interval.setSuffix(" 秒")
        self.set_interval.setSingleStep(0.5)
        form.addRow("请求间隔", self.set_interval)

        self.set_maxfail = QSpinBox()
        self.set_maxfail.setRange(1, 100)
        self.set_maxfail.setValue(int(s.max_consecutive_failures))
        form.addRow("连续失败暂停阈值", self.set_maxfail)

        self.set_disk = QSpinBox()
        self.set_disk.setRange(50, 100000)
        self.set_disk.setValue(int(s.disk_threshold_mb))
        self.set_disk.setSuffix(" MB")
        self.set_disk.setSingleStep(100)
        form.addRow("磁盘暂停阈值", self.set_disk)

        self.set_r18 = QCheckBox("抓取 R-18 作品")
        self.set_r18.setChecked(s.r18_enabled)
        form.addRow("", self.set_r18)

        self.set_blacklist = QLineEdit(",".join(s.tag_blacklist))
        self.set_blacklist.setPlaceholderText("多个标签用英文逗号分隔")
        form.addRow("标签黑名单", self.set_blacklist)

        # ---- 网络 ----
        form = make_page()
        self.set_ua = QLineEdit(s.user_agent)
        form.addRow("User-Agent", self.set_ua)

        self.proxy_enabled = QCheckBox("启用代理")
        self.proxy_enabled.setChecked(s.proxy.enabled)
        form.addRow("", self.proxy_enabled)
        self.proxy_scheme = QComboBox()
        self.proxy_scheme.addItems(["http", "socks5"])
        self.proxy_scheme.setCurrentText(s.proxy.scheme)
        form.addRow("代理协议", self.proxy_scheme)
        self.proxy_host = QLineEdit(s.proxy.host)
        form.addRow("代理主机", self.proxy_host)
        self.proxy_port = QSpinBox()
        self.proxy_port.setRange(1, 65535)
        self.proxy_port.setValue(int(s.proxy.port))
        form.addRow("代理端口", self.proxy_port)
        self.proxy_user = QLineEdit(s.proxy.username)
        form.addRow("代理用户名", self.proxy_user)
        self.proxy_pass = QLineEdit()
        self.proxy_pass.setEchoMode(QLineEdit.Password)
        self.proxy_pass.setPlaceholderText("留空表示不修改" if s.proxy.password else "")
        form.addRow("代理密码", self.proxy_pass)

        # ---- 登录 Cookie ----
        form = make_page()
        self.cookie_edit = QPlainTextEdit()
        self.cookie_edit.setPlaceholderText("PHPSESSID=…（留空表示不修改）")
        self.cookie_edit.setFixedHeight(74)
        form.addRow("Cookie", self.cookie_edit)
        r = QHBoxLayout()
        r.setSpacing(8)
        btn_test = QPushButton("测试连接")
        btn_test.clicked.connect(self.test_cookie)
        btn_login = QPushButton("浏览器登录获取")
        btn_login.clicked.connect(self.browser_login)
        btn_clear = QPushButton("清除 Cookie")
        btn_clear.clicked.connect(self.clear_cookie)
        self.cookie_status = QLabel("")
        r.addWidget(btn_test); r.addWidget(btn_login); r.addWidget(btn_clear)
        r.addWidget(self.cookie_status, 1)
        form.addRow("", r)

        # ---- 系统 ----
        form = make_page()
        self.set_autostart = QCheckBox("随 Windows 开机自启（静默驻留托盘）")
        self.set_autostart.setChecked(s.autostart)
        form.addRow("", self.set_autostart)

        backup_row = QHBoxLayout()
        backup_row.setSpacing(8)
        btn_export = QPushButton("导出备份")
        btn_export.clicked.connect(self.export_backup)
        btn_import = QPushButton("导入备份")
        btn_import.clicked.connect(self.import_backup)
        backup_row.addWidget(btn_export)
        backup_row.addWidget(btn_import)
        backup_row.addStretch(1)
        form.addRow("配置迁移", backup_row)

        self.settings_nav.setCurrentRow(0)
        return page

    def export_backup(self):
        from ..core import backup
        path, _ = QFileDialog.getSaveFileName(
            self, "导出备份", "pixiv_scraper_backup.json", "JSON 文件 (*.json)")
        if not path:
            return
        self._collect_settings()
        backup.export_backup(path, self.settings, self.db.list_authors(), credentials.load_cookie())
        QMessageBox.information(
            self, "导出完成",
            f"备份已导出：\n{path}\n\n注意：文件明文包含 Cookie，请妥善保管，不要分享。")

    def import_backup(self):
        from ..core import backup
        path, _ = QFileDialog.getOpenFileName(self, "导入备份", "", "JSON 文件 (*.json)")
        if not path:
            return
        answer = QMessageBox.question(
            self, "导入备份",
            "导入将覆盖当前设置，并写入备份中的作者列表与 Cookie。\n\n是否继续？")
        if answer != QMessageBox.Yes:
            return
        try:
            result = backup.import_backup(path, self.db)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return
        self.refresh_authors()
        self._refresh_conn_badge()
        QMessageBox.information(
            self, "导入完成",
            f"已导入 {result['authors_total']} 位作者（新增 {result['authors_added']}），"
            "设置与 Cookie 已恢复。\n请重启程序使全部设置生效。")

    def _pick_dir(self, edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "选择目录", edit.text() or "")
        if path:
            edit.setText(path)

    def test_cookie(self):
        cookie = self.cookie_edit.toPlainText().strip() or credentials.load_cookie()
        if not cookie:
            self.cookie_status.setText("未配置 Cookie")
            return
        self.cookie_status.setText("测试中…")
        probe = self._collect_settings(save_paths=False)
        client = PixivClient(probe, cookie)
        ok, msg = client.test_connection()
        client.close()
        _set_class(self.cookie_status, "status-ok" if ok else "status-err")
        self.cookie_status.setText(("✔ " if ok else "✘ ") + msg)
        if ok:
            self._refresh_conn_badge()

    def clear_cookie(self):
        credentials.clear_cookie()
        self.cookie_status.setText("已清除")
        self._refresh_conn_badge()

    def browser_login(self):
        from .login_window import launch_browser_login
        self.cookie_status.setText("请在弹出窗口中完成登录…")
        launch_browser_login(self, lambda: (
            self.cookie_status.setText("登录窗口已关闭，可点击「测试连接」验证"),
            self._refresh_conn_badge(),
        ))

    def _refresh_conn_badge(self):
        try:
            ok = bool(credentials.load_cookie())
        except Exception:
            ok = False
        _set_class(self.conn_badge, "badge-ok" if ok else "badge-warn")
        self.conn_badge.setText("已配置 Cookie" if ok else "未配置 Cookie")

    def _collect_settings(self, save_paths: bool = True):
        s = self.settings
        s.epub_root = self.set_epub.text().strip()
        s.md_root = self.set_md.text().strip()
        s.illust_md_root = self.set_illust_md.text().strip()
        s.illust_root = self.set_illust_root.text().strip()
        s.fetch_time = self.set_time.time().toString("HH:mm")
        s.request_interval = float(self.set_interval.value())
        s.max_consecutive_failures = int(self.set_maxfail.value())
        s.disk_threshold_mb = int(self.set_disk.value())
        s.user_agent = self.set_ua.text().strip() or s.user_agent
        s.name_pattern = self.set_pattern.currentData() or "{title}.{id}"
        s.r18_enabled = self.set_r18.isChecked()
        s.tag_blacklist = [t.strip() for t in self.set_blacklist.text().split(",") if t.strip()]
        s.autostart = self.set_autostart.isChecked()
        s.proxy.enabled = self.proxy_enabled.isChecked()
        s.proxy.scheme = self.proxy_scheme.currentText()
        s.proxy.host = self.proxy_host.text().strip()
        s.proxy.port = int(self.proxy_port.value())
        s.proxy.username = self.proxy_user.text().strip()
        if self.proxy_pass.text():
            s.proxy.password = self.proxy_pass.text()
        return s

    def save_settings(self):
        old_md_mode = self.settings.md_separate
        cookie_text = self.cookie_edit.toPlainText().strip()
        if cookie_text:
            credentials.save_cookie(cookie_text)
            self.cookie_edit.clear()
        s = self._collect_settings()
        s.theme = self.theme_combo.currentData() or "light"
        persist_settings(s)
        set_autostart(s.autostart)
        self._apply_theme(s.theme)
        self._refresh_conn_badge()
        if s.md_separate != old_md_mode:
            report = paths.migrate_md_files(s, self.db.list_authors(), self.logger)
            QMessageBox.information(
                self, "MD 索引迁移",
                f"已迁移 {report['moved']} 个，冲突保留 {report['conflict']} 个，失败 {report['failed']} 个"
            )
        QMessageBox.information(self, "设置", "已保存")

    # ============================================================ 主题
    def _on_theme_combo_changed(self, _index=None):
        mode = self.theme_combo.currentData() or "light"
        self.settings.theme = mode
        self._apply_theme(mode)
        try:
            persist_settings(self.settings)
        except Exception:
            pass

    def _toggle_theme(self):
        cur = resolve_mode(getattr(self.settings, "theme", "light"))
        nxt = "dark" if cur == "light" else "light"
        self.settings.theme = nxt
        idx = self.theme_combo.findData(nxt)
        if idx >= 0:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(idx)
            self.theme_combo.blockSignals(False)
        self._apply_theme(nxt)
        try:
            persist_settings(self.settings)
        except Exception:
            pass

    def _apply_theme(self, mode: str):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            apply_theme(app, mode)
        self._decorate()
        if self.on_theme_changed:
            try:
                self.on_theme_changed()
            except Exception:
                pass
        try:
            self.refresh_authors()
        except Exception:
            pass

    def _decorate(self):
        """按当前主题刷新所有图标与顶栏按钮。"""
        t = tokens(current_mode())
        accent, dim, on_accent = t["accent_text"], t["text_dim"], t["on_accent"]

        self.btn_run.setIcon(icon("play", on_accent, 15))
        self.btn_stop.setIcon(icon("stop", t["danger"], 15))

        for btn, name in (
            (self.btn_theme, "moon" if current_mode() == "light" else "sun"),
        ):
            btn.setIcon(icon(name, dim, 17))

        self._decorate_nav(self.nav, ["users", "activity", "search", "sliders"])
        self._decorate_nav(self.settings_nav,
                           ["palette", "folder", "gauge", "globe", "key", "database"])

    def _decorate_nav(self, nav: QListWidget, names: list):
        """选中项用主色，未选中项用次级灰。"""
        t = tokens(current_mode())
        active, idle = t["accent_text"], t["text_dim"]
        cur = nav.currentRow()
        for i, name in enumerate(names):
            item = nav.item(i)
            if item:
                item.setIcon(icon(name, active if i == cur else idle, 17))

    # ================================================================ 其他
    def select_settings_tab(self):
        self.stack.setCurrentIndex(3)
        self.nav.blockSignals(True)
        self.nav.setCurrentRow(3)
        self.nav.blockSignals(False)

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def force_quit(self):
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self, "确认", "抓取任务进行中，确定退出？") != QMessageBox.Yes:
                return
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
