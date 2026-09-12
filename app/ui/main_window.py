import os
import re

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QDoubleSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from .. import credentials
from ..autostart import set_autostart
from ..core import paths
from ..core.models import status_label
from ..pixiv.client import PixivClient
from .worker import ScrapeWorker

_AUTHOR_ID_RE = re.compile(r"^\d+$")


class BatchImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量导入作者 ID")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("每行或逗号分隔一个作者数字 ID："))
        self.edit = QPlainTextEdit()
        layout.addWidget(self.edit)
        row = QHBoxLayout()
        btn_file = QPushButton("从文件导入…")
        btn_file.clicked.connect(self._from_file)
        btn_ok = QPushButton("确定")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(btn_file)
        row.addStretch(1)
        row.addWidget(btn_ok)
        row.addWidget(btn_cancel)
        layout.addLayout(row)
        self.resize(420, 280)

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
        self.setWindowTitle("Pixiv 小说自动抓取器")
        self.resize(960, 660)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.setCentralWidget(self.tabs)
        self._build_authors_tab()
        self._build_tasks_tab()
        self._build_search_tab()
        self._build_settings_tab()
        self.refresh_authors()
        self.refresh_failed()

    # ---------------- 作者 ----------------
    def _build_authors_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.author_input = QLineEdit()
        self.author_input.setPlaceholderText("作者数字 ID")
        btn_add = QPushButton("添加")
        btn_add.setProperty("class", "primary")
        btn_add.clicked.connect(self.add_author)
        btn_batch = QPushButton("批量导入")
        btn_batch.clicked.connect(self.batch_import)
        row.addWidget(self.author_input, 1)
        row.addWidget(btn_add)
        row.addWidget(btn_batch)
        layout.addLayout(row)

        self.author_table = QTableWidget(0, 5)
        self.author_table.setHorizontalHeaderLabels(["昵称", "作者 ID", "状态", "最近抓取", "已收录作品"])
        self.author_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.author_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.author_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.author_table.setAlternatingRowColors(True)
        self.author_table.verticalHeader().setVisible(False)
        self.author_table.setShowGrid(False)
        layout.addWidget(self.author_table, 1)

        row2 = QHBoxLayout()
        btn_toggle = QPushButton("启用/停用")
        btn_toggle.clicked.connect(self.toggle_author)
        btn_del = QPushButton("删除")
        btn_del.setProperty("class", "danger")
        btn_del.clicked.connect(self.delete_author)
        row2.addStretch(1)
        row2.addWidget(btn_toggle)
        row2.addWidget(btn_del)
        layout.addLayout(row2)
        self.tabs.addTab(page, "作者")

    def add_author(self):
        text = self.author_input.text().strip()
        if not _AUTHOR_ID_RE.match(text):
            QMessageBox.warning(self, "输入无效", "作者 ID 应为纯数字")
            return
        if self.db.add_author(text, pending_validate=True):
            self.author_input.clear()
            self.refresh_authors()
        else:
            QMessageBox.information(self, "已存在", "该作者已在列表中")

    def batch_import(self):
        dlg = BatchImportDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        ids = dlg.ids()
        added = sum(1 for i in ids if self.db.add_author(i, pending_validate=True))
        QMessageBox.information(self, "导入结果", f"共 {len(ids)} 个：成功 {added}，重复/无效 {len(ids) - added}")
        self.refresh_authors()

    def _selected_author_id(self):
        row = self.author_table.currentRow()
        if row < 0:
            return None
        return self.author_table.item(row, 1).text()

    def toggle_author(self):
        aid = self._selected_author_id()
        if not aid:
            return
        authors = {a["author_id"]: a for a in self.db.list_authors()}
        cur = authors.get(aid)
        if cur:
            self.db.set_author_enabled(aid, not cur["enabled"])
            self.refresh_authors()

    def delete_author(self):
        aid = self._selected_author_id()
        if not aid:
            return
        if QMessageBox.question(self, "确认", f"删除作者 {aid}？本地已下载文件不会被删除。") == QMessageBox.Yes:
            self.db.remove_author(aid)
            self.refresh_authors()

    def refresh_authors(self):
        authors = self.db.list_authors()
        self.author_table.setRowCount(len(authors))
        for i, a in enumerate(authors):
            name = a.get("author_name") or ("（待校验）" if a.get("pending_validate") else "（未知）")
            items = [
                name,
                a["author_id"],
                "启用" if a["enabled"] else "停用",
                a.get("last_fetch_at") or "-",
                str(self.db.author_novel_count(a["author_id"])),
            ]
            for c, text in enumerate(items):
                self.author_table.setItem(i, c, QTableWidgetItem(str(text)))

    # ---------------- 任务 ----------------
    def _build_tasks_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
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
        self.run_status.setProperty("class", "hint")
        btn_logs = QPushButton("打开日志目录")
        btn_logs.clicked.connect(self.open_log_dir)
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_stop)
        row.addWidget(self.run_status, 1)
        row.addWidget(btn_logs)
        layout.addLayout(row)

        self.progress_view = QPlainTextEdit()
        self.progress_view.setProperty("class", "log")
        self.progress_view.setReadOnly(True)
        self.progress_view.setMaximumBlockCount(2000)
        layout.addWidget(self.progress_view, 2)

        fail_label = QLabel("失败队列")
        fail_label.setProperty("class", "hint")
        layout.addWidget(fail_label)
        self.failed_table = QTableWidget(0, 4)
        self.failed_table.setHorizontalHeaderLabels(["作品 ID", "标题", "失败原因", "状态"])
        self.failed_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.failed_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.failed_table.setAlternatingRowColors(True)
        self.failed_table.verticalHeader().setVisible(False)
        self.failed_table.setShowGrid(False)
        layout.addWidget(self.failed_table, 1)
        self.tabs.addTab(page, "任务")

    def open_log_dir(self):
        from ..logging_setup import log_dir
        os.startfile(log_dir())

    def start_fetch(self):
        if self.worker and self.worker.isRunning():
            return
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.run_status.setText("抓取中…")
        self.progress_view.appendPlainText("=== 任务开始 ===")
        self.worker = ScrapeWorker(self.settings, self.db, self.logger, self)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_report.connect(self.on_finished)
        self.worker.start()

    def stop_fetch(self):
        if not (self.worker and self.worker.isRunning()):
            return
        if QMessageBox.question(self, "确认中止", "确定要中止当前抓取任务？\n当前作品会处理完再停止，已下载的内容不会丢失。") != QMessageBox.Yes:
            return
        self.worker.stop()
        self.btn_stop.setEnabled(False)
        self.run_status.setText("正在中止…")
        self.progress_view.appendPlainText("※ 收到中止请求，等待当前作品处理完成…")

    def on_progress(self, event: dict):
        kind = event.get("kind")
        if kind == "author_start":
            self.progress_view.appendPlainText(f"→ 处理作者 {event.get('author_name')}（{event.get('author_id')}）")
        elif kind == "novel_done":
            tag = "重打包" if event.get("updated") else "新下载"
            self.progress_view.appendPlainText(f"  [{tag}] {event.get('title')}（{event.get('novel_id')}）")
        elif kind == "author_done":
            self.progress_view.appendPlainText(
                f"  完成：新增 {event.get('new')} 更新 {event.get('updated')} 跳过 {event.get('skipped')} 失败 {event.get('failed')}"
            )

    def on_finished(self, report):
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.run_status.setText("就绪")
        summary = (
            f"=== 任务结束：新增 {report.new_count}，更新 {report.updated_count}，"
            f"跳过 {report.skipped_count}，失败 {report.failed_count}，远端删除 {report.deleted_count} ==="
        )
        if report.aborted_reason:
            summary += f"\n{report.aborted_reason}"
        self.progress_view.appendPlainText(summary)
        self.refresh_authors()
        self.refresh_failed()
        if self.on_fetch_done:
            self.on_fetch_done(report)

    def refresh_failed(self):
        failed = self.db.failed_novels()
        self.failed_table.setRowCount(len(failed))
        for i, n in enumerate(failed):
            for c, text in enumerate([
                n["novel_id"], n.get("title") or "-", n.get("error_reason") or "-",
                status_label(n["status"]),
            ]):
                self.failed_table.setItem(i, c, QTableWidgetItem(str(text)))

    # ---------------- 检索 ----------------
    def _build_search_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("按标题 / 作者昵称 / 标签搜索")
        self.search_input.returnPressed.connect(self.do_search)
        btn = QPushButton("搜索")
        btn.setProperty("class", "primary")
        btn.clicked.connect(self.do_search)
        row.addWidget(self.search_input, 1)
        row.addWidget(btn)
        layout.addLayout(row)
        self.search_table = QTableWidget(0, 4)
        self.search_table.setHorizontalHeaderLabels(["标题", "作者", "状态", "本地路径"])
        self.search_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.search_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.search_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.search_table.setAlternatingRowColors(True)
        self.search_table.verticalHeader().setVisible(False)
        self.search_table.setShowGrid(False)
        self.search_table.doubleClicked.connect(self.open_novel_dir)
        layout.addWidget(self.search_table, 1)
        hint = QLabel("双击结果打开所在目录")
        hint.setProperty("class", "hint")
        layout.addWidget(hint)
        self.tabs.addTab(page, "检索")

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

    def open_novel_dir(self):
        row = self.search_table.currentRow()
        if row < 0:
            return
        path = self.search_table.item(row, 3).text()
        if path and os.path.exists(path):
            os.startfile(os.path.dirname(path))

    # ---------------- 设置 ----------------
    def _build_settings_tab(self):
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)
        s = self.settings

        self.set_epub = QLineEdit(s.epub_root)
        btn_epub = QPushButton("浏览…")
        btn_epub.clicked.connect(lambda: self._pick_dir(self.set_epub))
        r = QHBoxLayout(); r.addWidget(self.set_epub, 1); r.addWidget(btn_epub)
        form.addRow("EPUB 目录", r)

        self.set_md = QLineEdit(s.md_root)
        self.set_md.setPlaceholderText("留空 = 与 EPUB 同目录")
        btn_md = QPushButton("浏览…")
        btn_md.clicked.connect(lambda: self._pick_dir(self.set_md))
        r = QHBoxLayout(); r.addWidget(self.set_md, 1); r.addWidget(btn_md)
        form.addRow("MD 索引目录", r)

        self.set_time = QTimeEdit(QTime.fromString(s.fetch_time, "HH:mm"))
        self.set_time.setDisplayFormat("HH:mm")
        form.addRow("每日抓取时间", self.set_time)

        self.set_interval = QDoubleSpinBox()
        self.set_interval.setRange(0.5, 60.0)
        self.set_interval.setValue(float(s.request_interval))
        self.set_interval.setSuffix(" 秒")
        form.addRow("请求间隔", self.set_interval)

        self.set_maxfail = QSpinBox()
        self.set_maxfail.setRange(1, 100)
        self.set_maxfail.setValue(int(s.max_consecutive_failures))
        form.addRow("连续失败暂停阈值", self.set_maxfail)

        self.set_disk = QSpinBox()
        self.set_disk.setRange(50, 100000)
        self.set_disk.setValue(int(s.disk_threshold_mb))
        self.set_disk.setSuffix(" MB")
        form.addRow("磁盘暂停阈值", self.set_disk)

        self.set_ua = QLineEdit(s.user_agent)
        form.addRow("User-Agent", self.set_ua)

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

        self.cookie_edit = QPlainTextEdit()
        self.cookie_edit.setPlaceholderText("PHPSESSID=…（留空表示不修改）")
        self.cookie_edit.setMaximumHeight(60)
        form.addRow("Cookie", self.cookie_edit)
        r = QHBoxLayout()
        btn_test = QPushButton("测试连接")
        btn_test.clicked.connect(self.test_cookie)
        btn_login = QPushButton("浏览器登录获取")
        btn_login.clicked.connect(self.browser_login)
        btn_clear = QPushButton("清除 Cookie")
        btn_clear.clicked.connect(self.clear_cookie)
        self.cookie_status = QLabel("")
        r.addWidget(btn_test); r.addWidget(btn_login); r.addWidget(btn_clear); r.addWidget(self.cookie_status, 1)
        form.addRow("", r)

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

        self.set_r18 = QCheckBox("抓取 R-18 作品")
        self.set_r18.setChecked(s.r18_enabled)
        form.addRow("", self.set_r18)

        self.set_blacklist = QLineEdit(",".join(s.tag_blacklist))
        self.set_blacklist.setPlaceholderText("多个标签用英文逗号分隔")
        form.addRow("标签黑名单", self.set_blacklist)

        self.set_autostart = QCheckBox("随 Windows 开机自启")
        self.set_autostart.setChecked(s.autostart)
        form.addRow("", self.set_autostart)

        btn_save = QPushButton("保存设置")
        btn_save.setProperty("class", "primary")
        btn_save.clicked.connect(self.save_settings)
        form.addRow("", btn_save)

        backup_row = QHBoxLayout()
        btn_export = QPushButton("导出备份")
        btn_export.clicked.connect(self.export_backup)
        btn_import = QPushButton("导入备份")
        btn_import.clicked.connect(self.import_backup)
        backup_row.addWidget(btn_export)
        backup_row.addWidget(btn_import)
        backup_row.addStretch(1)
        form.addRow("配置迁移", backup_row)
        self.tabs.addTab(page, "设置")

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
        probe = self._collect_settings(save_paths=False)
        client = PixivClient(probe, cookie)
        ok, msg = client.test_connection()
        client.close()
        self.cookie_status.setProperty("class", "status-ok" if ok else "status-err")
        self.cookie_status.style().unpolish(self.cookie_status)
        self.cookie_status.style().polish(self.cookie_status)
        self.cookie_status.setText(("✔ " if ok else "✘ ") + msg)

    def clear_cookie(self):
        credentials.clear_cookie()
        self.cookie_status.setText("已清除")

    def browser_login(self):
        from .login_window import launch_browser_login
        self.cookie_status.setText("请在弹出窗口中完成登录…")
        launch_browser_login(self, lambda: self.cookie_status.setText("登录窗口已关闭，可点击「测试连接」验证"))

    def _collect_settings(self, save_paths: bool = True):
        s = self.settings
        s.epub_root = self.set_epub.text().strip()
        s.md_root = self.set_md.text().strip()
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
        from ..config import save_settings as persist
        persist(s)
        set_autostart(s.autostart)
        if s.md_separate != old_md_mode:
            report = paths.migrate_md_files(s, self.db.list_authors(), self.logger)
            QMessageBox.information(
                self, "MD 索引迁移",
                f"已迁移 {report['moved']} 个，冲突保留 {report['conflict']} 个，失败 {report['failed']} 个"
            )
        QMessageBox.information(self, "设置", "已保存")

    # ---------------- 其他 ----------------
    def select_settings_tab(self):
        self.tabs.setCurrentIndex(3)

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def force_quit(self):
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self, "确认", "抓取任务进行中，确定退出？") != QMessageBox.Yes:
                return
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
