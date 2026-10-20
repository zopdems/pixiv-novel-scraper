import os
import re

from PySide6.QtCore import Qt, QTime
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QMenu, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QDoubleSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTimeEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import credentials
from ..autostart import set_autostart
from ..config import app_data_dir, reset_advanced_settings, save_settings as persist_settings
from ..core import paths
from ..core.db import DISABLE_REASON_MANUAL
from ..core.models import STATUS_LABELS, Status, status_label
from ..core.rank_db import RANK_MODES, RankDatabase, mode_label
from ..core.utils import extract_author_id
from ..pixiv.client import PixivClient
from .author_status import (AUTHOR_DELETED_LABEL, AUTHOR_STATUS_ACTIVE,
                            AUTHOR_STATUS_DELETED, AUTHOR_STATUS_UNKNOWN,
                            STATUS_TOKEN, author_display_name,
                            decorate_name_item, resolve_author_status,
                            status_text)
from .icons import app_mark, icon
from .ranking_page import RankingPageMixin
from .theme import apply_theme, current_mode, resolve_mode, tokens
from .widgets import (ChipRail, LogDrawer, MetricTile, SegmentedControl,
                      dot, hairline, set_dot)
from .worker import RebuildMdWorker, ScrapeWorker

_AUTHOR_ID_RE = re.compile(r"^\d+$")

# 主页面在 QStackedWidget 里的顺序（与 _build_*_page 的添加顺序一致）。
# 之前各处直接写数字索引，导致「跳到设置页」实际跳到了排行榜页。
PAGE_AUTHORS, PAGE_TASKS, PAGE_SEARCH, PAGE_RANKING, PAGE_SETTINGS = range(5)

# 一级导航：(文案, 图标名)。文案与图标写在一起，避免两处列表长度不一致导致图标错位
# （历史 bug：设置子导航有 10 项，却只传了 6 个图标，前 6 项全部错配）。
MAIN_SECTIONS = [
    ("作者", "users"),
    ("任务", "activity"),
    ("检索", "search"),
    ("排行", "gauge"),
    ("设置", "sliders"),
]

# 设置页小节：(文案, 图标名, 一句话说明)。新版是一条连续滚动的长文档，
# 胶囊条只负责定位，说明文字放在小节标题下面。
SETTINGS_SECTIONS = [
    ("外观", "palette", "主题立即生效并自动保存"),
    ("存储路径", "folder", "EPUB / Markdown / 图片的落盘位置"),
    ("抓取策略", "gauge", "触发时机、请求频率与过滤规则"),
    ("文件输出", "file-text", "目录结构、文件命名与 Markdown 索引"),
    ("内容编排", "book", "EPUB 内的信息页、封面与章节"),
    ("更新策略", "refresh", "何时重新生成，远端删除如何处理"),
    ("网络", "globe", "User-Agent 与代理"),
    ("登录 Cookie", "key", "凭据只存本机；导出备份时明文包含"),
    ("系统", "database", "开机自启、备份与配置迁移"),
    ("排行榜", "activity", "榜单抓取的开关、范围与记录方式"),
]

# 命令栏响应式断点：窄屏优先保证「导航 + 主操作」
_BREAK_TITLE = 920      # 低于此宽度隐藏品牌名
_BREAK_SEARCH = 1020    # 低于此宽度隐藏命令栏搜索（检索页仍有完整搜索）


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
        out = []
        for s in re.split(r"[\s,，、;；]+", text):
            aid = extract_author_id(s)
            if aid:
                out.append(aid)
        return out


class MainWindow(QMainWindow, RankingPageMixin):
    def __init__(self, settings, db, logger):
        super().__init__()
        self.settings = settings
        self.db = db
        self.logger = logger
        self.worker = None
        self.md_worker = None
        self.rank_worker = None
        self.last_report = None
        try:
            self.rank_db = RankDatabase(os.path.join(app_data_dir(), "rank.db"), logger)
        except Exception:
            self.rank_db = None
        self.on_fetch_done = None
        self.on_ranking_done = None
        self.on_theme_changed = None

        self.setWindowTitle("Pixiv 小说抓取器")
        # 初始尺寸按屏幕可用区域算：1366×768 在 125% 缩放下逻辑高度只有 ~614，
        # 写死 1140×740 会超出可视区。最小尺寸也同步下调，保证小屏不被裁切。
        self.setMinimumSize(880, 560)
        self._fit_to_screen()

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # 命令栏在最上，内容区全幅铺满，状态与日志收在底部。
        # 旧版是「顶栏 + 左侧导航 + 内容卡片」，导航占了固定 186px 且把层级压深一层。
        root.addWidget(self._build_commandbar())

        # 抽屉要先建：排行榜页初始化时会把自己的日志挂到 drawer 的第 2 个视图上
        self.drawer = LogDrawer(["抓取日志", "榜单日志"])
        self.progress_view = self.drawer.view(0)
        self.rank_log_view = self.drawer.view(1)
        self.drawer.btn_clear.clicked.connect(self._clear_current_log)

        self.stack = QStackedWidget()
        self.stack.setObjectName("content")
        root.addWidget(self.stack, 1)

        root.addWidget(self._build_dock())
        root.addWidget(self.drawer)

        self.stack.addWidget(self._build_authors_page())
        self.stack.addWidget(self._build_tasks_page())
        self.stack.addWidget(self._build_search_page())
        self.stack.addWidget(self._build_ranking_page())
        self.stack.addWidget(self._build_settings_page())

        self._bind_shortcuts()

        self.nav.setCurrentIndex(0)
        self.nav.indexChanged.connect(self.stack.setCurrentIndex)
        self.refresh_authors()
        self.refresh_failed()
        self._refresh_conn_badge()
        self._decorate()
        self._apply_responsive()

    # ================================================================ 骨架
    def _fit_to_screen(self):
        """按屏幕可用区域决定初始尺寸。

        首选 1140×740，但不超过可用区域的 92%，避免在高缩放比 / 小屏笔记本上
        窗口底部被任务栏遮住或右侧溢出。
        """
        w, h = 1140, 740
        try:
            from PySide6.QtWidgets import QApplication
            scr = QApplication.primaryScreen() or self.screen()
            avail = scr.availableGeometry() if scr else None
        except Exception:
            avail = None
        if avail is not None and avail.width() > 0 and avail.height() > 0:
            w = max(880, min(w, int(avail.width() * 0.92)))
            h = max(560, min(h, int(avail.height() * 0.92)))
        self.resize(w, h)

    def _build_commandbar(self) -> QFrame:
        """顶部命令栏：品牌 + 一级分段导航 + 全局搜索 + 主操作。

        把「立即抓取」提到命令栏，任何页面都能一键开始，不用先切到任务页。
        """
        bar = QFrame()
        bar.setObjectName("commandbar")
        bar.setFixedHeight(tokens(current_mode())["topbar_h"])
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(10)

        mark = QLabel()
        mark.setPixmap(app_mark(28))
        mark.setFixedSize(28, 28)
        h.addWidget(mark)

        self.brand_label = QLabel("Pixiv 小说抓取器")
        self.brand_label.setProperty("class", "brand")
        h.addWidget(self.brand_label)

        # 一级导航：横向分段控件，取代旧版左侧 QListWidget
        self.nav = SegmentedControl([lb for lb, _ic in MAIN_SECTIONS])
        h.addWidget(self.nav)
        h.addStretch(1)

        # 全局搜索：回车直接跳到检索页并出结果，省掉「切页 → 找输入框」两步
        self.global_search = QLineEdit()
        self.global_search.setProperty("class", "globalsearch")
        self.global_search.setPlaceholderText("搜索本地作品…")
        self.global_search.setFixedWidth(168)
        self.global_search.returnPressed.connect(self._run_global_search)
        h.addWidget(self.global_search)

        self.btn_run = QPushButton("立即抓取")
        self.btn_run.setProperty("class", "primary")
        self.btn_run.setToolTip("按当前名单立刻跑一轮同步（Ctrl+Shift+F）")
        self.btn_run.clicked.connect(self.start_fetch)
        h.addWidget(self.btn_run)

        self.btn_stop = QPushButton("中止")
        self.btn_stop.setProperty("class", "danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setVisible(False)
        self.btn_stop.clicked.connect(self.stop_fetch)
        h.addWidget(self.btn_stop)

        self.btn_theme = QPushButton()
        self.btn_theme.setProperty("class", "icon")
        self.btn_theme.setToolTip("切换浅色 / 深色主题")
        self.btn_theme.clicked.connect(self._toggle_theme)
        h.addWidget(self.btn_theme)

        btn_hide = QPushButton("隐藏")
        btn_hide.setProperty("class", "ghost")
        btn_hide.setToolTip("最小化到系统托盘，后台继续按计划运行")
        btn_hide.clicked.connect(self.hide)
        h.addWidget(btn_hide)
        return bar

    def _build_dock(self) -> QFrame:
        """底部状态栏：连接状态 + 运行状态 + 上次结果 + 待处理 + 日志入口。

        旧版这些信息一部分在顶栏徽标、一部分在任务页卡片里、一部分在 QStatusBar，
        现在统一收在一处，任何页面都能看到。
        """
        dock = QFrame()
        dock.setObjectName("dock")
        dock.setFixedHeight(tokens(current_mode())["dock_h"])
        h = QHBoxLayout(dock)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(10)

        self.conn_dot = dot("idle")
        h.addWidget(self.conn_dot)
        self.conn_label = QLabel("未配置 Cookie")
        self.conn_label.setProperty("class", "dock")
        h.addWidget(self.conn_label)

        self.btn_cookie_fix = QPushButton("去配置")
        self.btn_cookie_fix.setProperty("class", "ghost")
        self.btn_cookie_fix.setToolTip("前往「设置 → 登录 Cookie」导入 Cookie")
        self.btn_cookie_fix.clicked.connect(lambda: self.open_settings_section("登录 Cookie"))
        h.addWidget(self.btn_cookie_fix)

        h.addStretch(1)

        self.run_status = QLabel("就绪")
        self.run_status.setProperty("class", "badge-idle")
        h.addWidget(self.run_status)

        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setFixedWidth(70)
        self.busy.setTextVisible(False)
        self.busy.setVisible(False)
        h.addWidget(self.busy)

        self.dock_last = QLabel("尚未执行过抓取")
        self.dock_last.setProperty("class", "dock")
        h.addWidget(self.dock_last)

        self.dock_pending = QLabel("")
        self.dock_pending.setProperty("class", "dock")
        h.addWidget(self.dock_pending)

        self.btn_log = QPushButton("日志")
        self.btn_log.setProperty("class", "ghost")
        self.btn_log.setToolTip("展开 / 收起底部日志（Ctrl+L）")
        self.btn_log.clicked.connect(self.drawer.toggle)
        h.addWidget(self.btn_log)
        return dock

    def _clear_current_log(self):
        view = self.drawer.view(self.drawer.tabs.currentIndex())
        if view is not None:
            view.clear()

    def _bind_shortcuts(self):
        """键盘直达：一级导航 Ctrl+1~5，搜索 Ctrl+K，日志 Ctrl+L。"""
        for i in range(min(5, len(MAIN_SECTIONS))):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda idx=i: self.nav.setCurrentIndex(idx))
        sc_search = QShortcut(QKeySequence("Ctrl+K"), self)
        sc_search.activated.connect(self._focus_global_search)
        sc_log = QShortcut(QKeySequence("Ctrl+L"), self)
        sc_log.activated.connect(self.drawer.toggle)
        sc_fetch = QShortcut(QKeySequence("Ctrl+Shift+F"), self)
        sc_fetch.activated.connect(self.start_fetch)

    def _focus_global_search(self):
        self.nav.setCurrentIndex(PAGE_SEARCH)
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _run_global_search(self):
        kw = self.global_search.text().strip()
        if not kw:
            return
        self.nav.setCurrentIndex(PAGE_SEARCH)
        self.search_input.setText(kw)
        self.do_search()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive()

    def _apply_responsive(self):
        """窄屏下按优先级裁剪命令栏内容：导航与主操作永远保留。"""
        w = self.width()
        self.brand_label.setVisible(w >= _BREAK_TITLE)
        show_search = w >= _BREAK_SEARCH
        self.global_search.setVisible(show_search)

    # -------------------------------------------------------- 页面骨架工具
    def _page(self, title: str, subtitle: str):
        """页面外壳：标题区（含右侧操作位）+ 一条发丝线。

        返回 (页面, 外层纵向布局, 标题行布局)，标题行可继续塞按钮。
        """
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(20, 16, 20, 12)
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
        outer.addWidget(hairline())
        return w, outer, head

    def _block(self, outer, vertical=False, pad=(20, 10)):
        """往页面里追加一个分区（工具条 / 指标行），下面补一条发丝线。

        重构后不再用「浮起的圆角卡片」分区，而是靠留白 + 发丝线，
        页面因此可以从上到下连续阅读，也不会出现卡片之间的双重描边。
        """
        w = QWidget()
        lay = QVBoxLayout(w) if vertical else QHBoxLayout(w)
        lay.setContentsMargins(pad[0], pad[1], pad[0], pad[1])
        lay.setSpacing(8)
        outer.addWidget(w)
        outer.addWidget(hairline())
        return lay

    def _section_bar(self, outer, title: str):
        """分区标题行：左侧标题 + 右侧操作位，下面补一条发丝线。"""
        lay = self._block(outer, pad=(20, 8))
        lab = QLabel(title)
        lab.setProperty("class", "section-title")
        lay.addWidget(lab)
        lay.addSpacing(4)
        lay.addStretch(1)
        return lay

    # ------------------------------------------------------------ 行菜单
    def _attach_row_menu(self, table: QTableWidget, actions: list):
        """给表格行挂右键菜单，省掉「选中行 → 把鼠标移到卡片顶部按钮」这一段路径。

        actions: [(菜单文案, 回调)]。回调执行前会先把光标所在行设为当前行，
        因此回调可以直接复用原有的「作用于选中行」逻辑，不需要改任何业务代码。
        """
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos, t=table, a=actions: self._show_row_menu(t, a, pos))

    def _show_row_menu(self, table: QTableWidget, actions: list, pos):
        row = table.rowAt(pos.y())
        if row < 0:
            return
        if row != table.currentRow():
            table.selectRow(row)
        menu = QMenu(self)
        for label, callback in actions:
            act = menu.addAction(label)
            act.triggered.connect(lambda _checked=False, fn=callback: fn())
        menu.exec(table.viewport().mapToGlobal(pos))

    @staticmethod
    def _wrap_layout(layout) -> QWidget:
        """把布局包成 QWidget，便于放进 QFormLayout 的某一行。"""
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.addLayout(layout)
        return w

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
        table.verticalHeader().setDefaultSectionSize(tokens(current_mode())["row_h"])
        table.setShowGrid(True)
        table.setSortingEnabled(True)
        table.cellDoubleClicked.connect(self._on_author_cell_double_clicked)
        # 常用操作直接右键，不必回到上方按钮区
        self._attach_row_menu(table, [
            ("打开 Pixiv 主页", self.open_author_page),
            ("启用 / 停用", self.toggle_author),
            ("从名单删除", self.delete_author),
        ])
        return table

    def _build_authors_page(self):
        page, outer, _head = self._page(
            "作者管理", "维护需要同步的小说 / 插画作者名单，双击昵称可打开 Pixiv 主页")

        # 指标行：不进表格也能判断「现在什么状态」
        self.m_authors = MetricTile("名单作者")
        self.m_works = MetricTile("已收录作品")
        self.m_deleted = MetricTile("远端已删除")
        self.m_failed = MetricTile("待处理")
        mrow = self._block(outer, pad=(20, 12))
        for tile in (self.m_authors, self.m_works, self.m_deleted, self.m_failed):
            mrow.addWidget(tile)

        bar = self._block(outer)
        self.author_input = QLineEdit()
        self.author_input.setPlaceholderText("作者数字 ID，或粘贴 pixiv.net/users/… 主页链接")
        self.author_input.returnPressed.connect(self.add_author)
        self.author_input.setFixedWidth(280)

        btn_add = QPushButton("添加")
        btn_add.setProperty("class", "primary")
        btn_add.clicked.connect(self.add_author)
        btn_batch = QPushButton("批量导入")
        btn_batch.setProperty("class", "subtle")
        btn_batch.clicked.connect(self.batch_import)

        bar.addWidget(self.author_input)
        bar.addWidget(btn_add)
        bar.addWidget(btn_batch)
        bar.addStretch(1)

        self.btn_rebuild_md = QPushButton("重建索引")
        self.btn_rebuild_md.setToolTip("按当前设置重新生成全部作者的 Markdown 索引（不联网、不重新下载作品）")
        self.btn_rebuild_md.clicked.connect(self.rebuild_md_index)

        btn_open = QPushButton("打开主页")
        btn_open.clicked.connect(self.open_author_page)
        btn_toggle = QPushButton("启用 / 停用")
        btn_toggle.clicked.connect(self.toggle_author)
        btn_del = QPushButton("删除")
        btn_del.setProperty("class", "danger")
        btn_del.clicked.connect(self.delete_author)
        bar.addWidget(self.btn_rebuild_md)
        bar.addWidget(btn_open)
        bar.addWidget(btn_toggle)
        bar.addWidget(btn_del)

        # 二级切换也用分段控件，取代 QTabWidget
        sub = self._block(outer, pad=(20, 8))
        self.author_tabs = SegmentedControl(["小说作者", "插画作者", "已删除作品"])
        sub.addWidget(self.author_tabs)
        sub.addStretch(1)

        self.author_stack = QStackedWidget()
        self.novel_author_table = self._make_author_table(
            ["昵称", "作者 ID", "状态", "最近抓取", "已收录小说", "已删单篇", "已删系列"])
        self.illust_author_table = self._make_author_table(
            ["昵称", "作者 ID", "状态", "最近抓取", "已收录插画", "已删插画"])
        self.author_stack.addWidget(self.novel_author_table)
        self.author_stack.addWidget(self.illust_author_table)
        self.author_stack.addWidget(self._build_deleted_works_tab())
        # 每次切到「已删除作品」都重置为收起，不继承上次的展开状态
        self.author_tabs.indexChanged.connect(self.author_stack.setCurrentIndex)
        self.author_tabs.indexChanged.connect(self._on_author_tab_changed)
        outer.addWidget(self.author_stack, 1)
        return page

    def _on_author_tab_changed(self, index: int):
        if index == 2:
            self.deleted_tree.collapseAll()

    def _build_deleted_works_tab(self) -> QWidget:
        """「已删除作品」标签页：按作者分组列出远端已删除的作品。"""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 12, 20, 12)
        v.setSpacing(8)

        self.deleted_summary = QLabel("")
        self.deleted_summary.setProperty("class", "hint")
        self.deleted_summary.setWordWrap(True)
        v.addWidget(self.deleted_summary)

        self.deleted_tree = QTreeWidget()
        self.deleted_tree.setColumnCount(5)
        self.deleted_tree.setHeaderLabels(["作品 / 作者", "作品 ID", "分类", "发布时间", "更新时间"])
        self.deleted_tree.setAlternatingRowColors(True)
        self.deleted_tree.setEditTriggers(QTableWidget.NoEditTriggers)
        self.deleted_tree.setVerticalScrollMode(QTreeWidget.ScrollPerPixel)
        header = self.deleted_tree.header()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        for i, wdt in enumerate([340, 110, 190, 130, 130]):
            self.deleted_tree.setColumnWidth(i, wdt)
        self.deleted_tree.itemDoubleClicked.connect(self._on_deleted_double_clicked)
        v.addWidget(self.deleted_tree, 1)
        return w

    def _active_author_kind(self):
        """当前作者名单类型；位于「已删除作品」标签页时返回 None。"""
        idx = self.author_tabs.currentIndex()
        if idx == 0:
            return "novel"
        if idx == 1:
            return "illust"
        return None

    def _active_author_table(self):
        if self._active_author_kind() == "novel":
            return self.novel_author_table
        if self._active_author_kind() == "illust":
            return self.illust_author_table
        return None

    def _on_author_cell_double_clicked(self, row, col):
        # 双击任意列均可跳转主页（含已注销作者，页面为空属预期，不做拦截）
        table = self.sender()
        item = table.item(row, 1)
        if item is None:
            return
        aid = item.text().strip()
        if aid.isdigit():
            self._open_pixiv_user(aid)

    @staticmethod
    def _open_pixiv_user(aid: str):
        import webbrowser
        webbrowser.open(f"https://www.pixiv.net/users/{aid}")

    def add_author(self):
        aid = extract_author_id(self.author_input.text())
        if not aid:
            QMessageBox.warning(
                self, "输入无效",
                "请填写作者数字 ID，或直接粘贴作者主页链接，例如：\n"
                "https://www.pixiv.net/users/104854837")
            return
        kind = self._active_author_kind()
        if kind is None:
            QMessageBox.information(self, "提示", "请先切换到「小说作者」或「插画作者」标签页再添加。")
            return
        if self.db.add_author(aid, pending_validate=True, kind=kind):
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
        if kind is None:
            QMessageBox.information(self, "提示", "请先切换到「小说作者」或「插画作者」标签页再导入。")
            return
        label = "小说" if kind == "novel" else "插画"
        added = sum(1 for i in ids if self.db.add_author(i, pending_validate=True, kind=kind))
        QMessageBox.information(self, "导入结果",
                                f"导入到{label}名单：共 {len(ids)} 个，成功 {added}，重复/无效 {len(ids) - added}")
        self.refresh_authors()

    def _selected_author_id(self):
        table = self._active_author_table()
        if table is None:
            return None
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
            # 停用一律记为「手动」，与抓取时判定的「账号已注销」区分开
            self.db.set_author_enabled(aid, not cur["enabled"], kind=kind,
                                       reason=DISABLE_REASON_MANUAL)
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
        tk = tokens(current_mode())
        accent, muted = tk["accent"], tk["text_muted"]
        table.setSortingEnabled(False)
        table.setRowCount(len(authors))
        for i, a in enumerate(authors):
            aid = a["author_id"]
            display, st = author_display_name(a)
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

            table.setItem(i, 0, decorate_name_item(QTableWidgetItem(display), st, accent, muted))

            state_item = QTableWidgetItem(status_text(st))
            state_item.setForeground(QColor(tk[STATUS_TOKEN[st]]))
            table.setItem(i, 2, state_item)

            table.setItem(i, 1, QTableWidgetItem(str(aid)))
            table.setItem(i, 3, QTableWidgetItem(str(a.get("last_fetch_at") or "-")))

            for offset, val in enumerate(counts):
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, val)
                table.setItem(i, 4 + offset, item)
        table.setSortingEnabled(True)

    # ------------------------------------------------ 已删除作品
    # ------------------------------------------------ 重建 Markdown 索引
    def rebuild_md_index(self):
        if not (self.settings.epub_root or "").strip():
            QMessageBox.warning(self, "提示", "请先在「设置 → 存储路径」里设置保存目录，再重建索引。")
            return
        extra = ""
        if not getattr(self.settings, "write_md_index", True):
            extra = ("\n\n注意：当前已关闭「生成作者索引 MD」，重建出的文件在后续抓取中不会再被更新，"
                     "如需持续维护请先在设置里开启。")
        answer = QMessageBox.question(
            self, "重建索引",
            f"将为全部作者重新生成 Markdown 索引：不联网、不重新下载作品，"
            f"只根据本地已有的作品信息重写。{extra}\n\n是否继续？")
        if answer != QMessageBox.Yes:
            return
        self.btn_rebuild_md.setEnabled(False)
        self.btn_rebuild_md.setText("重建中…")
        self.md_worker = RebuildMdWorker(self.settings, self.db, self.logger)
        self.md_worker.finished_report.connect(self._on_rebuild_md_done)
        self.md_worker.start()

    def _on_rebuild_md_done(self, report: dict):
        self.btn_rebuild_md.setEnabled(True)
        self.btn_rebuild_md.setText("重建索引")
        if report.get("error"):
            QMessageBox.warning(self, "重建索引", f"重建失败：{report['error']}")
            return
        QMessageBox.information(
            self, "重建索引",
            f"已处理 {report.get('authors', 0)} 位作者："
            f"写入 {report.get('written', 0)} 个，失败 {report.get('failed', 0)} 个。")

    def _fill_deleted_tree(self):
        tree = self.deleted_tree
        tk = tokens(current_mode())
        muted = tk["text_muted"]
        tree.clear()

        author_rows = {}
        for kind in ("novel", "illust"):
            for a in self.db.list_authors(kind=kind):
                author_rows[(kind, a["author_id"])] = a

        def make_work_item(kind, r):
            if kind == "novel":
                wid = str(r.get("novel_id") or "")
                if r.get("series_id"):
                    stitle = r.get("series_title") or wid
                    cat = f"系列《{stitle}》" + (f" 第{r['series_order']}话" if r.get("series_order") else "")
                else:
                    cat = "单篇"
            else:
                wid = str(r.get("illust_id") or "")
                cat = "插画 / 漫画"
            title = str(r.get("title") or "").strip() or "（无标题）"
            it = QTreeWidgetItem([
                title, wid, cat,
                str(r.get("publish_date") or "-")[:16].replace("T", " "),
                str(r.get("remote_updated_at") or "-")[:16].replace("T", " "),
            ])
            it.setForeground(0, QColor(muted))
            it.setForeground(1, QColor(muted))
            it.setToolTip(0, "该作品在远端已被删除，本地文件保留；双击打开作品页")
            it.setData(0, Qt.UserRole, ("work", kind, wid))
            return it

        data = {}
        for kind in ("novel", "illust"):
            works = self.db.deleted_novels() if kind == "novel" else self.db.deleted_illusts()
            for r in works:
                aid = str(r.get("author_id") or "").strip()
                data.setdefault((kind, aid), []).append(r)

        normal_nodes, deleted_nodes = [], []
        for (kind, aid), rows in sorted(data.items(), key=lambda kv: (-len(kv[1]), kv[0][0], kv[0][1])):
            row = author_rows.get((kind, aid))
            if row is None:
                display, st = AUTHOR_DELETED_LABEL, AUTHOR_STATUS_DELETED
            else:
                display, st = author_display_name(row)
            kind_label = "小说" if kind == "novel" else "插画"
            node = QTreeWidgetItem([
                f"{display} · ID {aid or '未知'} · {kind_label}（{len(rows)}）", "", "", "", ""
            ])
            node.setData(0, Qt.UserRole, ("author", kind, aid))
            for r in rows:
                node.addChild(make_work_item(kind, r))
            (deleted_nodes if st == AUTHOR_STATUS_DELETED else normal_nodes).append(node)

        for node in normal_nodes:
            node.setForeground(0, QColor(tk["text"]))
            tree.addTopLevelItem(node)

        if deleted_nodes:
            group = QTreeWidgetItem([
                AUTHOR_DELETED_LABEL, "", f"{len(deleted_nodes)} 位作者", "", ""
            ])
            group.setForeground(0, QColor(muted))
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setToolTip(0, "账号已停用 / 已注销或信息缺失；双击作者行仍可打开其主页")
            for node in deleted_nodes:
                node.setForeground(0, QColor(muted))
                group.addChild(node)
            tree.addTopLevelItem(group)

        # 默认全部收起：只有用户主动点击分组才会展开
        tree.collapseAll()
        total_works = sum(len(v) for v in data.values())
        if not data:
            self.deleted_summary.setText(
                "暂无已删除作品。远端被删除的作品会自动归集到这里（本地文件保留）。")
        else:
            self.deleted_summary.setText(
                f"共 {len(data)} 位作者有已删除作品，合计 {total_works} 部/幅；"
                f"其中 {AUTHOR_DELETED_LABEL}作者 {len(deleted_nodes)} 位。"
                "本地文件均已保留。双击作者行打开主页，双击作品行打开作品页。")

    def _on_deleted_double_clicked(self, item, col):
        data = item.data(0, Qt.UserRole)
        if not data:
            return  # 「已注销」分组行本身不跳转
        tag = data[0]
        if tag == "author":
            aid = str(data[2] or "")
            if aid.isdigit():
                self._open_pixiv_user(aid)
        elif tag == "work":
            _, kind, wid = data
            import webbrowser
            if kind == "novel":
                webbrowser.open(f"https://www.pixiv.net/novel/show.php?id={wid}")
            else:
                webbrowser.open(f"https://www.pixiv.net/artworks/{wid}")

    def refresh_authors(self):
        self._fill_author_table(self.novel_author_table, "novel")
        self._fill_author_table(self.illust_author_table, "illust")
        try:
            self._fill_deleted_tree()
        except Exception:
            self.deleted_summary.setText("已删除作品列表加载失败，请重试或查看日志。")
        try:
            n = len(self.db.list_authors(kind="novel"))
            m = len(self.db.list_authors(kind="illust"))
            self.m_authors.set_value(str(n + m))
            self.m_authors.set_caption(f"小说 {n} 位 · 插画 {m} 位")
        except Exception:
            pass
        self._refresh_overview_tiles()

    def _refresh_overview_tiles(self):
        """刷新「已收录 / 已删除 / 待处理」三个指标，并同步底部状态栏。"""
        try:
            stats = self.db.stats() or {}
            total = sum(int(v) for v in stats.values())
            self.m_works.set_value(str(total))
            root = self.settings.epub_root or "未设置"
            self.m_works.set_caption(f"输出目录：{root}")
        except Exception:
            pass
        try:
            gone = len(self.db.deleted_novels()) + len(self.db.deleted_illusts())
            self.m_deleted.set_value(str(gone), "warn" if gone else "")
            self.m_deleted.set_caption("本地文件均已保留")
        except Exception:
            pass
        try:
            pending = len(self.db.failed_novels()) + len(self.db.failed_illusts())
            self.m_failed.set_value(str(pending), "err" if pending else "ok")
            self.m_failed.set_caption("可在任务页重试或移除" if pending else "没有需要处理的记录")
            self.dock_pending.setText(f"{pending} 项待处理" if pending else "无待处理")
        except Exception:
            pass

    # ================================================================ 任务
    def _build_tasks_page(self):
        page, outer, _head = self._page(
            "任务", "运行日志已收进底部抽屉（Ctrl+L），在任何页面都能看到实时进度")

        # 上一次任务的结果用三个数字说话，不再只给一行文字
        self.t_new = MetricTile("上次新增")
        self.t_upd = MetricTile("上次更新")
        self.t_fail = MetricTile("上次失败")
        self.t_gone = MetricTile("远端已删除")
        mrow = self._block(outer, pad=(20, 12))
        for tile in (self.t_new, self.t_upd, self.t_fail, self.t_gone):
            mrow.addWidget(tile)

        block = self._block(outer, vertical=True)
        self.last_run_label = QLabel("尚未执行过抓取")
        self.last_run_label.setProperty("class", "hint")
        self.last_run_label.setWordWrap(True)
        block.addWidget(self.last_run_label)

        bar = self._section_bar(outer, "失败 / 远端已删除")
        self.failed_count = QLabel("")
        self.failed_count.setProperty("class", "hint")
        bar.addWidget(self.failed_count)
        btn_retry_one = QPushButton("重新抓取")
        btn_retry_one.setProperty("class", "subtle")
        btn_retry_one.setToolTip("把选中项重置为「待下载」，下一轮抓取会重新尝试")
        btn_retry_one.clicked.connect(self.retry_selected_item)
        btn_drop_one = QPushButton("移除记录")
        btn_drop_one.setProperty("class", "subtle")
        btn_drop_one.setToolTip("仅从本列表移除索引记录，本地已下载的文件不会被删除")
        btn_drop_one.clicked.connect(self.remove_selected_item)
        btn_logs = QPushButton("打开日志目录")
        btn_logs.setProperty("class", "ghost")
        btn_logs.clicked.connect(self.open_log_dir)
        bar.addWidget(btn_retry_one)
        bar.addWidget(btn_drop_one)
        bar.addWidget(btn_logs)

        self.failed_table = QTableWidget(0, 4)
        self.failed_table.setHorizontalHeaderLabels(["作品 ID", "标题", "说明", "状态"])
        self.failed_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.failed_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.failed_table.setAlternatingRowColors(True)
        self.failed_table.verticalHeader().setVisible(False)
        self.failed_table.verticalHeader().setDefaultSectionSize(tokens(current_mode())["row_h"])
        self.failed_table.setShowGrid(False)
        self._attach_row_menu(self.failed_table, [
            ("重试这一条", self.retry_selected_item),
            ("从列表移除", self.remove_selected_item),
        ])
        outer.addWidget(self.failed_table, 1)
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
        self.btn_stop.setVisible(True)
        self.busy.setVisible(True)
        _set_class(self.run_status, "badge")
        self.run_status.setText("抓取中…")
        set_dot(self.conn_dot, "run")
        # 任务一开始就把日志抽屉展开，进度不用再去任务页找
        if not self.drawer.is_expanded():
            self.drawer.set_expanded(True)
        self.drawer.tabs.setCurrentIndex(0)
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
        self.btn_stop.setVisible(False)
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
        self._refresh_conn_badge()

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
        self.last_report = report
        self._update_last_run_label()
        self.refresh_authors()
        self.refresh_failed()
        self._refresh_task_tiles(report)
        if getattr(report, "auth_expired", False):
            # 登录失效：切换连接徽标为警告态，并明确提示需要重新导入 Cookie
            self.conn_label.setText("Cookie 已失效")
            set_dot(self.conn_dot, "err")
        if self.on_fetch_done:
            self.on_fetch_done(report)

    def _update_last_run_label(self):
        """在任务页保留上一次抓取的结果摘要，切走再回来也能看到。"""
        report = getattr(self, "last_report", None)
        if report is None:
            self.last_run_label.setText("尚未执行过抓取")
            self.dock_last.setText("尚未执行过抓取")
            return
        when = (getattr(report, "finished_at", "") or getattr(report, "started_at", "") or "")[:16]
        if getattr(report, "auth_expired", False):
            self.last_run_label.setText(f"上次任务 {when}：登录已失效，任务中止，请重新导入 Cookie")
            _set_class(self.last_run_label, "status-err")
            self.dock_last.setText(f"上次 {when}：登录已失效")
            return
        if getattr(report, "aborted_reason", ""):
            self.last_run_label.setText(f"上次任务 {when}：已中止 — {report.aborted_reason}")
            _set_class(self.last_run_label, "status-err")
            self.dock_last.setText(f"上次 {when}：已中止")
            return
        failed = getattr(report, "failed_count", 0) + getattr(report, "illust_failed", 0)
        text = (f"上次任务 {when}：新增 {report.new_count} / 更新 {report.updated_count} / "
                f"跳过 {report.skipped_count} / 失败 {failed} / 远端删除 {report.deleted_count}")
        _set_class(self.last_run_label, "status-err" if failed else "status-ok")
        self.last_run_label.setText(text)
        self.dock_last.setText(f"上次 {when} · 新增 {report.new_count}")

    def _refresh_task_tiles(self, report):
        self.t_new.set_value(str(getattr(report, "new_count", 0)),
                             "ok" if getattr(report, "new_count", 0) else "")
        self.t_upd.set_value(str(getattr(report, "updated_count", 0)))
        self.t_fail.set_value(str(getattr(report, "failed_count", 0) + getattr(report, "illust_failed", 0)),
                              "err" if (getattr(report, "failed_count", 0) + getattr(report, "illust_failed", 0)) else "ok")
        gone = getattr(report, "deleted_count", 0) + getattr(report, "illust_deleted", 0)
        self.t_gone.set_value(str(gone), "warn" if gone else "")

    def refresh_failed(self):
        rows = [
            ("novel", n["novel_id"], n.get("title") or "-",
             n.get("error_reason") or "-", status_label(n["status"]))
            for n in self.db.failed_novels()
        ]
        rows += [
            ("illust", i["illust_id"], "[图] " + (i.get("title") or "-"),
             i.get("error_reason") or "-", status_label(i["status"]))
            for i in self.db.failed_illusts()
        ]
        rows += [
            ("novel", n["novel_id"], n.get("title") or "-",
             "远端已删除（本地文件保留）", status_label(n["status"]))
            for n in self.db.deleted_novels()
        ]
        rows += [
            ("illust", i["illust_id"], "[图] " + (i.get("title") or "-"),
             "远端已删除（本地文件保留）", status_label(i["status"]))
            for i in self.db.deleted_illusts()
        ]
        self.failed_table.setSortingEnabled(False)
        self.failed_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            kind = row[0]
            for c, text in enumerate(row[1:]):
                item = QTableWidgetItem(str(text))
                if c == 0:
                    item.setData(Qt.UserRole, kind)  # 记录是小说还是插画
                self.failed_table.setItem(i, c, item)
        self.failed_table.setSortingEnabled(True)
        self.failed_count.setText(f"共 {len(rows)} 条" if rows else "暂无记录")
        self._refresh_overview_tiles()

    def _selected_failed_item(self):
        """返回 (kind, work_id)；未选中时返回 (None, None)。"""
        row = self.failed_table.currentRow()
        if row < 0:
            return None, None
        id_item = self.failed_table.item(row, 0)
        if id_item is None:
            return None, None
        return id_item.data(Qt.UserRole), id_item.text().strip()

    def retry_selected_item(self):
        kind, work_id = self._selected_failed_item()
        if not work_id:
            QMessageBox.information(self, "提示", "请先在下方列表里选中一条记录。")
            return
        if kind == "illust":
            self.db.set_illust_status(work_id, Status.PENDING)
        else:
            self.db.set_novel_status(work_id, Status.PENDING)
        self.logger.info("已将 %s 重置为待下载，下一轮抓取会重新尝试", work_id)
        self.refresh_failed()

    def remove_selected_item(self):
        kind, work_id = self._selected_failed_item()
        if not work_id:
            QMessageBox.information(self, "提示", "请先在下方列表里选中一条记录。")
            return
        answer = QMessageBox.question(
            self, "移除记录",
            f"将从列表移除作品 {work_id} 的索引记录。\n\n"
            "本地已下载的文件不会被删除，但重新抓取时该作品会被当作新作品处理。\n\n是否继续？")
        if answer != QMessageBox.Yes:
            return
        if kind == "illust":
            self.db.remove_illust(work_id)
        else:
            self.db.remove_novel(work_id)
        self.logger.info("已移除作品 %s 的索引记录（本地文件保留）", work_id)
        self.refresh_failed()

    # ================================================================ 检索
    def _build_search_page(self):
        page, outer, _head = self._page(
            "检索", "按标题 / 作者昵称 / 标签在本地库中查找已抓取的作品；命令栏搜索框也可直达")

        bar = self._block(outer)
        self.search_input = QLineEdit()
        self.search_input.setProperty("class", "search")
        self.search_input.setPlaceholderText("按标题 / 作者昵称 / 标签搜索，回车开始")
        self.search_input.returnPressed.connect(self.do_search)
        btn = QPushButton("搜索")
        btn.setProperty("class", "primary")
        btn.clicked.connect(self.do_search)
        bar.addWidget(self.search_input, 1)
        bar.addWidget(btn)

        self.search_type = QComboBox()
        self.search_type.addItem("全部类型", "")
        self.search_type.addItem("小说", "novel")
        self.search_type.addItem("插画 / 漫画", "illust")
        self.search_type.currentIndexChanged.connect(self.do_search)
        bar.addWidget(self.search_type)

        self.search_status = QComboBox()
        self.search_status.addItem("全部状态", "")
        for value, label in STATUS_LABELS.items():
            self.search_status.addItem(label, value)
        self.search_status.currentIndexChanged.connect(self.do_search)
        bar.addWidget(self.search_status)

        head = self._section_bar(outer, "搜索结果")
        self.search_count = QLabel("")
        self.search_count.setProperty("class", "hint")
        head.addWidget(self.search_count)
        btn_refresh = QPushButton("刷新")
        btn_refresh.setProperty("class", "subtle")
        btn_refresh.clicked.connect(self.do_search)
        head.addWidget(btn_refresh)

        self.search_table = QTableWidget(0, 5)
        self.search_table.setHorizontalHeaderLabels(["标题", "类型", "作者", "状态", "本地路径"])
        self.search_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.search_table.setColumnWidth(1, 110)
        self.search_table.setColumnWidth(2, 150)
        self.search_table.setColumnWidth(3, 90)
        self.search_table.setColumnWidth(4, 280)
        self.search_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.search_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.search_table.setAlternatingRowColors(True)
        self.search_table.verticalHeader().setVisible(False)
        self.search_table.verticalHeader().setDefaultSectionSize(tokens(current_mode())["row_h"])
        self.search_table.setShowGrid(False)
        self.search_table.doubleClicked.connect(self.open_novel_dir)
        self._attach_row_menu(self.search_table, [
            ("打开所在目录", self.open_novel_dir),
        ])
        outer.addWidget(self.search_table, 1)

        hint = QLabel("双击任意一行可打开该作品所在的目录")
        hint.setProperty("class", "hint")
        foot = self._block(outer, pad=(20, 8))
        foot.addWidget(hint)
        return page

    def do_search(self):
        kw = self.search_input.text().strip()
        if not kw:
            return
        kind_filter = (self.search_type.currentData() or "") if self.search_type else ""
        status_filter = (self.search_status.currentData() or "") if self.search_status else ""

        rows = []
        if kind_filter in ("", "novel"):
            rows += [("novel", n) for n in self.db.search_novels(kw)]
        if kind_filter in ("", "illust"):
            rows += [("illust", i) for i in self.db.search_illusts(kw)]
        if status_filter:
            rows = [r for r in rows if r[1].get("status") == status_filter]

        self.search_table.setRowCount(len(rows))
        for i, (ktype, rec) in enumerate(rows):
            for c, text in enumerate([
                rec.get("title") or "-",
                "小说" if ktype == "novel" else "插画 / 漫画",
                rec.get("author_name") or "-",
                status_label(rec["status"]),
                rec.get("local_path") or "-",
            ]):
                self.search_table.setItem(i, c, QTableWidgetItem(str(text)))
        self.search_count.setText(f"共 {len(rows)} 条")

    def open_novel_dir(self):
        row = self.search_table.currentRow()
        if row < 0:
            return
        item = self.search_table.item(row, 4)  # 本地路径列
        path = item.text() if item else ""
        if path and path != "-" and os.path.exists(path):
            os.startfile(os.path.dirname(path))

    # ================================================================ 设置
    def _build_settings_page(self):
        page, outer, head = self._page(
            "设置", "所有选项按小节连续排列，用下面的胶囊条直接跳转；改动后点「保存设置」")

        btn_save = QPushButton("保存设置")
        btn_save.setProperty("class", "primary")
        btn_save.clicked.connect(self.save_settings)
        head.addWidget(btn_save)

        btn_reset = QPushButton("恢复默认")
        btn_reset.setToolTip("把「高级设置」恢复为默认值，不影响存储路径 / Cookie / 代理 / 主题")
        btn_reset.clicked.connect(self.reset_advanced)
        head.addWidget(btn_reset)

        # 旧版是「左侧 10 项子导航 + 右侧 10 个堆叠页」，切换即换页、看不到全貌。
        # 新版是一份连续滚动的长文档，胶囊条只做定位，滚动时自动回写当前小节。
        self.settings_rail = ChipRail([lb for lb, *_ in SETTINGS_SECTIONS])
        outer.addWidget(self.settings_rail)
        outer.addWidget(hairline())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        col = QWidget()
        self.settings_col = QVBoxLayout(col)
        self.settings_col.setContentsMargins(0, 0, 0, 0)
        self.settings_col.setSpacing(0)
        self.settings_col.addStretch(1)
        scroll.setWidget(col)
        outer.addWidget(scroll, 1)
        self.settings_scroll = scroll
        self._settings_groups = []
        self._rail_guard = False
        self.settings_rail.indexChanged.connect(self._scroll_to_settings_group)
        scroll.verticalScrollBar().valueChanged.connect(self._sync_settings_rail)

        def make_group(title: str, caption: str = "") -> QFormLayout:
            """追加一个小节（标题 + 说明 + 表单），返回表单布局。"""
            gw = QWidget()
            gl = QVBoxLayout(gw)
            gl.setContentsMargins(20, 14, 20, 14)
            gl.setSpacing(6)
            lab = QLabel(title)
            lab.setProperty("class", "group-title")
            gl.addWidget(lab)
            if caption:
                cap = QLabel(caption)
                cap.setProperty("class", "hint")
                cap.setWordWrap(True)
                gl.addWidget(cap)
            form = QFormLayout()
            form.setSpacing(10)
            form.setHorizontalSpacing(16)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            gl.addLayout(form)

            pos = len(self._settings_groups) * 2
            self.settings_col.insertWidget(pos, gw)
            self.settings_col.insertWidget(pos + 1, hairline())
            self._settings_groups.append(gw)
            return form

        def section(form: QFormLayout, text: str) -> QLabel:
            lab = QLabel(text)
            lab.setProperty("class", "section-title")
            form.addRow(lab)
            return lab

        def hint(form: QFormLayout, text: str) -> QLabel:
            lab = QLabel(text)
            lab.setWordWrap(True)
            lab.setProperty("class", "hint")
            form.addRow("", lab)
            return lab

        def combo(items, current) -> QComboBox:
            c = QComboBox()
            for label, value in items:
                c.addItem(label, value)
            i = c.findData(current)
            c.setCurrentIndex(i if i >= 0 else 0)
            return c

        s = self.settings

        # ---- 外观 ----
        form = make_group("外观", "主题立即生效并自动保存")
        self.theme_combo = QComboBox()
        for label, value in [("浅色", "light"), ("深色", "dark"), ("跟随系统", "auto")]:
            self.theme_combo.addItem(label, value)
        idx = self.theme_combo.findData(getattr(s, "theme", "light"))
        self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        form.addRow("主题", self.theme_combo)
        hint(form, "也可以点命令栏右侧的月亮 / 太阳图标快速切换。")

        # ---- 存储路径 ----
        form = make_group("存储路径", "EPUB / Markdown / 图片的落盘位置")
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
        form = make_group("抓取策略", "触发时机、请求频率与过滤规则")
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

        section(form, "高级 · 触发与筛选")
        self.set_404 = combo([
            ("自动停用（默认）", "disable"),
            ("保持启用，仅记录", "keep"),
            ("本轮跳过，不改状态", "skip"),
        ], getattr(s, "author_404_action", "disable"))
        form.addRow("作者账号 404 时", self.set_404)
        hint(form, "判定依据是账号主体接口（已注销账号的作品列表接口仍返回 200 空列表，不可靠）；"
                   "单部作品被删除不受此项影响。")

        self.set_recheck = QSpinBox()
        self.set_recheck.setRange(0, 365)
        self.set_recheck.setSuffix(" 天")
        self.set_recheck.setValue(int(getattr(s, "disabled_recheck_days", 0) or 0))
        form.addRow("停用作者自动复查", self.set_recheck)
        hint(form, "0 = 不复查（默认）。设为 N 后，被停用满 N 天的作者会在下一轮自动重新启用并复查。")

        self.set_catchup = QSpinBox()
        self.set_catchup.setRange(0, 1440)
        self.set_catchup.setSuffix(" 分钟")
        self.set_catchup.setValue(int(getattr(s, "catch_up_window_minutes", 0) or 0))
        form.addRow("定时补跑窗口", self.set_catchup)
        hint(form, "0 = 当天任意时间启动都会补跑（默认）。设为 60 表示只在定时点后 1 小时内补跑。")

        self.set_cap = QSpinBox()
        self.set_cap.setRange(0, 9999)
        self.set_cap.setSpecialValueText("不限")
        self.set_cap.setValue(int(getattr(s, "max_novels_per_author", 0) or 0))
        form.addRow("每作者单次新增上限", self.set_cap)
        hint(form, "0 = 不限（默认）。仅限制「本次新发现」的作品数，本地缺失文件的补下载不受限制。")

        self.set_whitelist = QLineEdit(",".join(getattr(s, "tag_whitelist", []) or []))
        self.set_whitelist.setPlaceholderText("留空 = 不限制；多个标签用英文逗号分隔")
        form.addRow("标签白名单", self.set_whitelist)
        hint(form, "设了白名单后，只有命中其中至少一个标签的作品才会被抓取。与黑名单同时存在时，黑名单优先。")

        self.set_kw_in = QLineEdit(getattr(s, "keyword_include", "") or "")
        self.set_kw_in.setPlaceholderText("留空 = 不限制；多个词用英文逗号分隔")
        form.addRow("标题/简介包含", self.set_kw_in)

        self.set_kw_ex = QLineEdit(getattr(s, "keyword_exclude", "") or "")
        self.set_kw_ex.setPlaceholderText("留空 = 不限制；多个词用英文逗号分隔")
        form.addRow("标题/简介排除", self.set_kw_ex)

        self.set_minlen = QSpinBox()
        self.set_minlen.setRange(0, 1000000)
        self.set_minlen.setSingleStep(100)
        self.set_minlen.setSuffix(" 字")
        self.set_minlen.setSpecialValueText("不限")
        self.set_minlen.setValue(int(getattr(s, "min_text_length", 0) or 0))
        form.addRow("正文最短字数", self.set_minlen)
        hint(form, "0 = 不限（默认）。仅对小说生效，插画/漫画不受影响。")

        # ---- 文件输出 ----
        form = make_group("文件输出", "目录结构、文件命名与 Markdown 索引")
        section(form, "目录与命名")
        self.set_series_dir = QCheckBox("按「单篇 / 系列」分子目录")
        self.set_series_dir.setChecked(bool(getattr(s, "organize_by_series", True)))
        form.addRow("", self.set_series_dir)
        hint(form, "关闭后所有作品直接平铺在作者目录下，不再区分单篇与系列子目录。")

        self.set_mp_style = combo([
            ("每部作品一个子文件夹（默认）", "folder"),
            ("平铺到作者目录", "flat"),
        ], getattr(s, "illust_multipage_style", "folder"))
        form.addRow("多页插画存放方式", self.set_mp_style)

        self.set_namelen = QSpinBox()
        self.set_namelen.setRange(40, 200)
        self.set_namelen.setValue(int(getattr(s, "filename_max_length", 120) or 120))
        self.set_namelen.setSuffix(" 字符")
        form.addRow("文件名最大长度", self.set_namelen)
        hint(form, "超出部分会被截断；标题 / 作者 / 系列名的截断长度会按同比例缩放。")

        self.set_illrep = QLineEdit(getattr(s, "illegal_char_replacement", "_") or "_")
        self.set_illrep.setMaxLength(3)
        self.set_illrep.setPlaceholderText("默认 _")
        form.addRow("非法字符替换为", self.set_illrep)
        hint(form, r'用于替换 \ / : * ? " < > | 等不能出现在文件名里的字符。')

        section(form, "Markdown 索引")
        self.set_md_index = QCheckBox("生成作者索引 MD")
        self.set_md_index.setChecked(bool(getattr(s, "write_md_index", True)))
        form.addRow("", self.set_md_index)

        self.set_md_desc = QCheckBox("索引条目包含作品简介")
        self.set_md_desc.setChecked(bool(getattr(s, "md_include_description", True)))
        form.addRow("", self.set_md_desc)

        self.set_md_stats = QCheckBox("索引头部包含本轮抓取统计")
        self.set_md_stats.setChecked(bool(getattr(s, "md_include_stats", True)))
        form.addRow("", self.set_md_stats)

        self.set_md_sort = combo([
            ("按发布时间（默认）", "publish"),
            ("按更新时间", "updated"),
            ("按作品 ID", "id"),
        ], getattr(s, "md_single_sort", "publish"))
        form.addRow("单篇排序方式", self.set_md_sort)
        hint(form, "仅影响「单篇」段落的顺序；系列内始终按系列顺序号排序。")

        # ---- 内容编排 ----
        form = make_group("内容编排", "EPUB 内的信息页、封面与章节")
        section(form, "作品信息页")
        self.set_info_page = QCheckBox("生成「作品信息」页")
        self.set_info_page.setChecked(bool(getattr(s, "epub_include_info_page", True)))
        form.addRow("", self.set_info_page)
        hint(form, "关闭后 EPUB 直接从正文开始，简介 / 标签 / 时间等信息不再写入书中。")

        self.set_desc = QCheckBox("信息页包含作品简介")
        self.set_desc.setChecked(bool(getattr(s, "epub_include_description", True)))
        form.addRow("", self.set_desc)

        self.set_src_url = QCheckBox("信息页包含原文链接")
        self.set_src_url.setChecked(bool(getattr(s, "epub_include_source_url", True)))
        form.addRow("", self.set_src_url)

        self.set_tags_meta = QCheckBox("标签写入书籍元数据")
        self.set_tags_meta.setChecked(bool(getattr(s, "epub_include_tags_meta", True)))
        form.addRow("", self.set_tags_meta)
        hint(form, "关闭后标签不再写入 EPUB 元数据（阅读器里可能搜不到），信息页仍会显示标签。")

        section(form, "封面与插图")
        self.set_cover = QCheckBox("嵌入封面图")
        self.set_cover.setChecked(bool(getattr(s, "epub_include_cover", True)))
        form.addRow("", self.set_cover)

        self.set_cover_fb = QCheckBox("无封面时用第一张插图代替")
        self.set_cover_fb.setChecked(bool(getattr(s, "epub_cover_fallback", True)))
        form.addRow("", self.set_cover_fb)

        self.set_inserts = QCheckBox("下载正文内嵌插图")
        self.set_inserts.setChecked(bool(getattr(s, "epub_download_inserts", True)))
        form.addRow("", self.set_inserts)
        hint(form, "关闭后正文里的插图位置会保留原始标记文字，EPUB 体积显著变小。")

        section(form, "章节与语言")
        self.set_ch_title = combo([
            ("优先用章节标记（默认）", "marker"),
            ("统一用「第 N 页」", "page"),
            ("无标记时用正文首行", "first_line"),
        ], getattr(s, "chapter_title_style", "marker"))
        form.addRow("章节标题来源", self.set_ch_title)

        self.set_lang = QLineEdit(getattr(s, "epub_language", "ja") or "ja")
        self.set_lang.setMaxLength(12)
        self.set_lang.setPlaceholderText("默认 ja")
        form.addRow("书籍语言标记", self.set_lang)
        hint(form, "写入 EPUB 的 language 字段，如 ja / zh / en。仅影响阅读器识别，不影响正文。")

        # ---- 更新策略 ----
        form = make_group("更新策略", "何时重新生成，远端删除如何处理")
        section(form, "更新判定")
        self.set_upd_mode = combo([
            ("比较远端更新时间（默认）", "timestamp"),
            ("每次都重新生成", "always"),
            ("只补缺失文件", "never"),
        ], getattr(s, "update_mode", "timestamp"))
        form.addRow("更新判定方式", self.set_upd_mode)
        hint(form, "「每次都重新生成」会忽略时间戳重复打包，流量与耗时都会上升。")

        self.set_redl = QCheckBox("本地文件缺失时重新下载")
        self.set_redl.setChecked(bool(getattr(s, "redownload_missing", True)))
        form.addRow("", self.set_redl)

        self.set_retry = QCheckBox("每轮开头优先重试失败队列")
        self.set_retry.setChecked(bool(getattr(s, "retry_failed_each_run", True)))
        form.addRow("", self.set_retry)
        hint(form, "登录失效类失败不在此列，需要重新导入 Cookie 后才会重试。")

        self.set_repl_old = QCheckBox("新文件生成后删除旧文件")
        self.set_repl_old.setChecked(bool(getattr(s, "replace_old_file", True)))
        form.addRow("", self.set_repl_old)
        hint(form, "仅当新旧路径不同才会删除。关闭后可能同时留下新旧两份文件。")

        section(form, "删除与迁移")
        self.set_series_recheck = QSpinBox()
        self.set_series_recheck.setRange(1, 365)
        self.set_series_recheck.setSuffix(" 天")
        self.set_series_recheck.setValue(int(getattr(s, "series_recheck_days", 30) or 30))
        form.addRow("已删除系列复查间隔", self.set_series_recheck)

        self.set_del_action = combo([
            ("保留本地文件并标记（默认）", "keep"),
            ("保留本地文件，不做标记", "silent"),
        ], getattr(s, "remote_deleted_action", "keep"))
        form.addRow("作品在远端被删除时", self.set_del_action)
        hint(form, "两种方式都不会删除你本地的文件，区别只在于是否标记为「远端已删除」。")

        self.set_rename_mig = QCheckBox("作者改昵称时自动迁移目录")
        self.set_rename_mig.setChecked(bool(getattr(s, "author_rename_migrate", True)))
        form.addRow("", self.set_rename_mig)
        hint(form, "关闭后已下载的文件保留在原目录，但新下载的作品会落到新昵称目录。")

        # ---- 网络 ----
        form = make_group("网络", "User-Agent 与代理")
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
        form = make_group("登录 Cookie", "凭据只存本机；导出备份时明文包含")
        self.cookie_edit = QPlainTextEdit()
        self.cookie_edit.setPlaceholderText("PHPSESSID=…（留空表示不修改）")
        self.cookie_edit.setFixedHeight(tokens(current_mode())["edit_area_h"])
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
        hint(form, "未配置 Cookie 时，底部状态栏会出现「去配置」直达本小节。")

        # ---- 系统 ----
        form = make_group("系统", "开机自启、备份与配置迁移")
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

        section(form, "自动备份")
        self.set_auto_backup = QCheckBox("每轮抓取前自动备份")
        self.set_auto_backup.setChecked(bool(getattr(s, "auto_backup_enabled", True)))
        form.addRow("", self.set_auto_backup)

        self.set_backup_keep = QSpinBox()
        self.set_backup_keep.setRange(1, 50)
        self.set_backup_keep.setSuffix(" 份")
        self.set_backup_keep.setValue(int(getattr(s, "auto_backup_keep", 5) or 5))
        form.addRow("保留份数", self.set_backup_keep)
        hint(form, "备份保存在数据目录下的 backups 文件夹，"
                   "内容与手动导出一致、明文包含 Cookie，请勿外传或上传；超出份数的旧备份会自动删除。")

        # ---- 排行榜 ----
        form = make_group("排行榜", "榜单抓取的开关、范围与记录方式")
        section(form, "启用")
        self.set_ranking_enabled = QCheckBox("启用排行榜抓取（独立任务，不跟随每日抓取）")
        self.set_ranking_enabled.setChecked(bool(getattr(s, "ranking_enabled", False)))
        form.addRow("", self.set_ranking_enabled)
        hint(form, "开启后可在「排行」页面抓取榜单；也可随时手动点「抓取榜单」。"
                   "此项为独立任务，不会并入每日的名单抓取。")

        section(form, "每日自动抓取哪些榜单")
        self.rank_mode_boxes = {}
        modes_row = QHBoxLayout()
        modes_row.setSpacing(10)
        for m in RANK_MODES:
            box = QCheckBox(mode_label(m))
            box.setChecked(m in (getattr(s, "ranking_modes", []) or []))
            self.rank_mode_boxes[m] = box
            modes_row.addWidget(box)
        modes_row.addStretch(1)
        form.addRow("榜单", self._wrap_layout(modes_row))
        hint(form, "只影响定时任务每天自动抓的榜。「排行」页面里的榜单下拉是临时查看/手动抓取用的，"
                   "两者互不影响——手动抓哪个以页面下拉框为准。")

        self.set_ranking_r18 = QCheckBox("同时抓取 R-18 榜单（需要登录 Cookie，否则会 403）")
        self.set_ranking_r18.setChecked(bool(getattr(s, "ranking_include_r18", False)))
        form.addRow("", self.set_ranking_r18)
        hint(form, "注意：即使抓取了 R-18 榜单，正文是否下载仍受「抓取策略 → 包含 R-18」控制，"
                   "全局未开启时只会记录元信息。")

        section(form, "正文下载")
        self.set_ranking_download = QCheckBox("一并下载上榜作品的正文")
        self.set_ranking_download.setChecked(bool(getattr(s, "ranking_download", True)))
        form.addRow("", self.set_ranking_download)

        self.set_ranking_dl_mode = QComboBox()
        self.set_ranking_dl_mode.addItem("仅下载首次上榜 / 本地缺失的作品（推荐）", "new")
        self.set_ranking_dl_mode.addItem("每次都走更新判定（多一次详情请求）", "follow_update")
        idx = self.set_ranking_dl_mode.findData(
            str(getattr(s, "ranking_download_mode", "new") or "new"))
        self.set_ranking_dl_mode.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("下载范围", self.set_ranking_dl_mode)
        hint(form, "默认只在作品首次上榜时下载正文。一部作品连续霸榜时，后续每天对正文发 0 次请求，"
                   "不会重复下载。")

        self.set_ranking_lang = QCheckBox("只保留简体中文作品（R-18 榜同样处理）")
        self.set_ranking_lang.setChecked(bool(getattr(s, "ranking_language_filter", True)))
        form.addRow("", self.set_ranking_lang)
        hint(form, "实测综合榜里中文作品很少（每日榜 100 条中通常只有 0~1 条，男性向榜约 5 条），"
                   "过滤后部分榜单可能为空，属正常现象。")

        section(form, "抓取记录自动保存")
        self.set_ranking_csv = QCheckBox("每次抓取后自动生成一份 CSV")
        self.set_ranking_csv.setChecked(bool(getattr(s, "ranking_csv_enabled", True)))
        form.addRow("", self.set_ranking_csv)

        csv_row = QHBoxLayout()
        csv_row.setSpacing(8)
        self.set_ranking_csv_dir = QLineEdit()
        self.set_ranking_csv_dir.setPlaceholderText("留空则存放在数据目录下的 rank_csv")
        self.set_ranking_csv_dir.setText(str(getattr(s, "ranking_csv_dir", "") or ""))
        btn_csv_dir = QPushButton("浏览")
        btn_csv_dir.clicked.connect(self.pick_ranking_csv_dir)
        csv_row.addWidget(self.set_ranking_csv_dir, 1)
        csv_row.addWidget(btn_csv_dir)
        form.addRow("存放目录", self._wrap_layout(csv_row))
        hint(form, "文件名形如「排行榜_daily_2026-10-17.csv」；同一天重复抓取会覆盖，不会堆积重复文件。")

        self.set_ranking_txt = QCheckBox("同时追加一行到「排行榜抓取历史.txt」")
        self.set_ranking_txt.setChecked(bool(getattr(s, "ranking_txt_log", True)))
        form.addRow("", self.set_ranking_txt)
        hint(form, "放在数据目录的 logs 里，跟程序运行日志 run.log 挨着，双击就能看，"
                   "不需要打开本软件。每行一轮，内容和「排行榜 → 抓取历史」完全一致。")

        return page

    # ---------------------------------------------------- 设置页跳转联动
    def _scroll_to_settings_group(self, index: int):
        """胶囊条 → 滚动到对应小节。"""
        if self._rail_guard or not (0 <= index < len(self._settings_groups)):
            return
        self._rail_guard = True
        try:
            gw = self._settings_groups[index]
            self.settings_scroll.verticalScrollBar().setValue(max(0, gw.y() - 4))
        finally:
            self._rail_guard = False

    def _sync_settings_rail(self, _value: int = 0):
        """滚动 → 回写当前小节，让胶囊条始终反映实际位置。"""
        if self._rail_guard or not getattr(self, "_settings_groups", None):
            return
        v = self.settings_scroll.verticalScrollBar().value()
        cur = 0
        for i, gw in enumerate(self._settings_groups):
            if gw.y() <= v + 12:
                cur = i
        if self.settings_rail.currentIndex() != cur:
            self._rail_guard = True
            try:
                self.settings_rail.setCurrentIndex(cur)
            finally:
                self._rail_guard = False

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
        self.conn_label.setText("已配置 Cookie" if ok else "未配置 Cookie")
        set_dot(self.conn_dot, "ok" if ok else "warn")
        try:
            self.btn_cookie_fix.setVisible(not ok)
        except Exception:
            pass

    def open_settings_section(self, label: str):
        """跳到设置页的指定小节（默认跳到「登录 Cookie」所在项）。"""
        names = [lb for lb, *_ in SETTINGS_SECTIONS]
        idx = names.index(label) if label in names else 0
        self.stack.setCurrentIndex(PAGE_SETTINGS)
        self.nav.setCurrentIndex(PAGE_SETTINGS)
        self.settings_rail.setCurrentIndex(idx)

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

        # ---- 高级设置 ----
        s.author_404_action = self.set_404.currentData() or "disable"
        s.disabled_recheck_days = int(self.set_recheck.value())
        s.catch_up_window_minutes = int(self.set_catchup.value())
        s.max_novels_per_author = int(self.set_cap.value())
        s.tag_whitelist = [t.strip() for t in self.set_whitelist.text().split(",") if t.strip()]
        s.keyword_include = self.set_kw_in.text().strip()
        s.keyword_exclude = self.set_kw_ex.text().strip()
        s.min_text_length = int(self.set_minlen.value())
        s.organize_by_series = self.set_series_dir.isChecked()
        s.illust_multipage_style = self.set_mp_style.currentData() or "folder"
        s.filename_max_length = int(self.set_namelen.value())
        s.illegal_char_replacement = self.set_illrep.text().strip() or "_"
        s.write_md_index = self.set_md_index.isChecked()
        s.md_include_description = self.set_md_desc.isChecked()
        s.md_include_stats = self.set_md_stats.isChecked()
        s.md_single_sort = self.set_md_sort.currentData() or "publish"
        s.epub_include_info_page = self.set_info_page.isChecked()
        s.epub_include_description = self.set_desc.isChecked()
        s.epub_include_source_url = self.set_src_url.isChecked()
        s.epub_include_tags_meta = self.set_tags_meta.isChecked()
        s.epub_include_cover = self.set_cover.isChecked()
        s.epub_cover_fallback = self.set_cover_fb.isChecked()
        s.epub_download_inserts = self.set_inserts.isChecked()
        s.chapter_title_style = self.set_ch_title.currentData() or "marker"
        s.epub_language = self.set_lang.text().strip() or "ja"
        s.update_mode = self.set_upd_mode.currentData() or "timestamp"
        s.redownload_missing = self.set_redl.isChecked()
        s.retry_failed_each_run = self.set_retry.isChecked()
        s.replace_old_file = self.set_repl_old.isChecked()
        s.series_recheck_days = int(self.set_series_recheck.value())
        s.remote_deleted_action = self.set_del_action.currentData() or "keep"
        s.author_rename_migrate = self.set_rename_mig.isChecked()
        s.auto_backup_enabled = self.set_auto_backup.isChecked()
        s.auto_backup_keep = int(self.set_backup_keep.value())
        s.ranking_enabled = self.set_ranking_enabled.isChecked()
        s.ranking_modes = [m for m, box in self.rank_mode_boxes.items() if box.isChecked()]
        s.ranking_include_r18 = self.set_ranking_r18.isChecked()
        s.ranking_download = self.set_ranking_download.isChecked()
        s.ranking_download_mode = str(self.set_ranking_dl_mode.currentData() or "new")
        s.ranking_language_filter = self.set_ranking_lang.isChecked()
        s.ranking_csv_enabled = self.set_ranking_csv.isChecked()
        s.ranking_csv_dir = self.set_ranking_csv_dir.text().strip()
        s.ranking_txt_log = self.set_ranking_txt.isChecked()
        return s

    def pick_ranking_csv_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择排行榜 CSV 存放目录",
                                            self.set_ranking_csv_dir.text().strip() or "")
        if d:
            self.set_ranking_csv_dir.setText(d)

    def _sync_advanced_controls(self):
        """把 settings 中的高级设置回填到界面控件。"""
        s = self.settings

        def _set_combo(w: QComboBox, value):
            i = w.findData(value)
            w.setCurrentIndex(i if i >= 0 else 0)

        _set_combo(self.set_404, getattr(s, "author_404_action", "disable"))
        self.set_recheck.setValue(int(getattr(s, "disabled_recheck_days", 0) or 0))
        self.set_catchup.setValue(int(getattr(s, "catch_up_window_minutes", 0) or 0))
        self.set_cap.setValue(int(getattr(s, "max_novels_per_author", 0) or 0))
        self.set_whitelist.setText(",".join(getattr(s, "tag_whitelist", []) or []))
        self.set_kw_in.setText(getattr(s, "keyword_include", "") or "")
        self.set_kw_ex.setText(getattr(s, "keyword_exclude", "") or "")
        self.set_minlen.setValue(int(getattr(s, "min_text_length", 0) or 0))
        self.set_series_dir.setChecked(bool(getattr(s, "organize_by_series", True)))
        _set_combo(self.set_mp_style, getattr(s, "illust_multipage_style", "folder"))
        self.set_namelen.setValue(int(getattr(s, "filename_max_length", 120) or 120))
        self.set_illrep.setText(getattr(s, "illegal_char_replacement", "_") or "_")
        self.set_md_index.setChecked(bool(getattr(s, "write_md_index", True)))
        self.set_md_desc.setChecked(bool(getattr(s, "md_include_description", True)))
        self.set_md_stats.setChecked(bool(getattr(s, "md_include_stats", True)))
        _set_combo(self.set_md_sort, getattr(s, "md_single_sort", "publish"))
        self.set_info_page.setChecked(bool(getattr(s, "epub_include_info_page", True)))
        self.set_desc.setChecked(bool(getattr(s, "epub_include_description", True)))
        self.set_src_url.setChecked(bool(getattr(s, "epub_include_source_url", True)))
        self.set_tags_meta.setChecked(bool(getattr(s, "epub_include_tags_meta", True)))
        self.set_cover.setChecked(bool(getattr(s, "epub_include_cover", True)))
        self.set_cover_fb.setChecked(bool(getattr(s, "epub_cover_fallback", True)))
        self.set_inserts.setChecked(bool(getattr(s, "epub_download_inserts", True)))
        _set_combo(self.set_ch_title, getattr(s, "chapter_title_style", "marker"))
        self.set_lang.setText(getattr(s, "epub_language", "ja") or "ja")
        _set_combo(self.set_upd_mode, getattr(s, "update_mode", "timestamp"))
        self.set_redl.setChecked(bool(getattr(s, "redownload_missing", True)))
        self.set_retry.setChecked(bool(getattr(s, "retry_failed_each_run", True)))
        self.set_repl_old.setChecked(bool(getattr(s, "replace_old_file", True)))
        self.set_series_recheck.setValue(int(getattr(s, "series_recheck_days", 30) or 30))
        _set_combo(self.set_del_action, getattr(s, "remote_deleted_action", "keep"))
        self.set_rename_mig.setChecked(bool(getattr(s, "author_rename_migrate", True)))
        self.set_auto_backup.setChecked(bool(getattr(s, "auto_backup_enabled", True)))
        self.set_backup_keep.setValue(int(getattr(s, "auto_backup_keep", 5) or 5))

    def reset_advanced(self):
        answer = QMessageBox.question(
            self, "恢复默认",
            "将把全部「高级设置」恢复为默认值（等价于本软件原本的行为）。\n\n"
            "不受影响：存储路径、Cookie、代理、主题、开机自启、每日抓取时间等基础配置。\n\n"
            "是否继续？")
        if answer != QMessageBox.Yes:
            return
        reset_advanced_settings(self.settings)
        self._sync_advanced_controls()
        try:
            persist_settings(self.settings)
        except Exception:
            pass
        QMessageBox.information(self, "恢复默认", "已恢复默认值并保存。")

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
        """按当前主题刷新所有图标与状态色。"""
        t = tokens(current_mode())
        on_accent, dim, danger = t["on_accent"], t["text_dim"], t["danger"]

        self.btn_run.setIcon(icon("play", on_accent, 15))
        self.btn_stop.setIcon(icon("stop", danger, 15))
        self.btn_theme.setIcon(
            icon("moon" if current_mode() == "light" else "sun", dim, 17))

        self.nav.refresh_icons()
        self.author_tabs.refresh_icons()
        self.drawer.tabs.refresh_icons()
        if getattr(self, "rank_view_combo", None) is not None:
            self.rank_view_combo.refresh_icons()

    # ================================================================ 其他
    def select_settings_tab(self):
        """跳到「设置 → 登录 Cookie」（定时任务缺 Cookie 等场景会调用）。

        原实现写死索引 3，而 3 是「排行」页，导致提示去设置页却跳到了排行榜页。
        """
        self.open_settings_section("登录 Cookie")

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def force_quit(self):
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self, "确认", "抓取任务进行中，确定退出？") != QMessageBox.Yes:
                return
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
