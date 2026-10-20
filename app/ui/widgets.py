"""重构后的通用控件层。

旧版界面里「导航 / 视图切换 / 状态展示」分别由 QListWidget、QComboBox、
QLabel 各写一套，样式和交互都不统一。这里把它们收敛成四个可复用控件，
每个控件只负责一件事，配色与尺寸全部取自主题令牌：

    SegmentedControl   分段控件（一级导航 / 视图切换），带滑动指示器
    ChipRail           胶囊跳转条（设置页小节定位），横向滚动
    MetricTile         指标卡（页面顶部的关键数字）
    LogDrawer          底部日志抽屉，可展开 / 收起，含多个日志源

新增页面只需要组合这几个控件，不需要再写一遍样式。
"""

from PySide6.QtCore import (
    QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal,
)
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from .icons import icon
from .theme import MOTION, current_mode, tokens


def _polish(widget, cls: str):
    """切换样式类并立即生效（Qt 不会自动响应 property 变化）。"""
    widget.setProperty("class", cls)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def hairline() -> QFrame:
    """1px 发丝线：重构后用它分区，替代旧版「卡片 + 描边」。"""
    line = QFrame()
    line.setProperty("class", "rule")
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return line


def dot(tone: str = "idle") -> QLabel:
    """8px 状态圆点。tone: idle / ok / warn / err / run。"""
    lab = QLabel()
    lab.setProperty("class", f"dot-{tone}")
    lab.setFixedSize(8, 8)
    return lab


def set_dot(lab: QLabel, tone: str):
    _polish(lab, f"dot-{tone}")


# --------------------------------------------------------- 分段控件

class SegmentedControl(QFrame):
    """横向分段控件，替代旧版的左侧 QListWidget 导航与 QComboBox 视图切换。

    选中态是一块可滑动的白色指示片（180ms / OutCubic），不是「整块背景变色」，
    这样在浅底块上也能明确看出当前在哪一段。
    """

    indexChanged = Signal(int)

    def __init__(self, items, parent=None, icon_names=None):
        """
        items:       [(文案, 数据)] 或 [文案]
        icon_names:  [图标名]，长度需与 items 一致；不传则不带图标
        """
        super().__init__(parent)
        if items and isinstance(items[0], str):
            items = [(t, t) for t in items]
        self._items = list(items)
        self._icons = list(icon_names or [])
        self.setProperty("class", "seg")

        self._row = QHBoxLayout(self)
        m = 3
        self._row.setContentsMargins(m, m, m, m)
        self._row.setSpacing(2)

        # 指示片不进布局，自己按按钮位置摆；放在最底层让文字透出来
        self._ind = QFrame(self)
        self._ind.setProperty("class", "seg-ind")
        self._ind.hide()

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = []
        for i, (label, data) in enumerate(self._items):
            btn = QPushButton(label)
            btn.setProperty("class", "seg-btn")
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            btn.clicked.connect(lambda _c=False, idx=i: self.setCurrentIndex(idx))
            self._row.addWidget(btn)
            self._group.addButton(btn, i)
            self._buttons.append(btn)
            self._items[i] = (label, data)

        self._anim = QPropertyAnimation(self._ind, b"geometry", self)
        self._anim.setDuration(MOTION["base"])
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._first = True
        self._index = -1
        if self._buttons:
            self.setCurrentIndex(0)
        self.refresh_icons()
        QTimer.singleShot(0, lambda: self._move(False))

    # ---- 对外接口（对齐 QComboBox 的常用方法，替换时改动最小）----
    def count(self) -> int:
        return len(self._buttons)

    def currentIndex(self) -> int:
        return self._index

    def currentData(self):
        if 0 <= self._index < len(self._items):
            return self._items[self._index][1]
        return None

    def itemText(self, i: int) -> str:
        return self._items[i][0] if 0 <= i < len(self._items) else ""

    def button(self, i: int) -> QPushButton:
        return self._buttons[i] if 0 <= i < len(self._buttons) else None

    def setCurrentIndex(self, index: int):
        if index == self._index or not (0 <= index < len(self._buttons)):
            return
        self._index = index
        btn = self._buttons[index]
        btn.setChecked(True)
        self._move(True)
        self.indexChanged.emit(index)

    def refresh_icons(self):
        """主题切换后重画图标颜色。"""
        t = tokens(current_mode())
        active, idle = t["accent_text"], t["text_dim"]
        for i, btn in enumerate(self._buttons):
            name = self._icons[i] if i < len(self._icons) else None
            if not name:
                continue
            btn.setIcon(icon(name, active if i == self._index else idle, 15))

    # ---- 内部 ----
    def _move(self, animate: bool):
        if not (0 <= self._index < len(self._buttons)):
            self._ind.hide()
            return
        geo = self._buttons[self._index].geometry()
        if geo.width() <= 0 or geo.height() <= 0:
            self._ind.hide()
            return
        self._ind.show()
        self._ind.lower()          # 沉到按钮下面，让按钮文字透出来
        if not animate or self._first:
            self._ind.setGeometry(geo)
            self._first = False
            return
        self._anim.stop()
        self._anim.setStartValue(self._ind.geometry())
        self._anim.setEndValue(geo)
        self._anim.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, lambda: self._move(False))

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._move(False))


# --------------------------------------------------------- 胶囊跳转条

class ChipRail(QScrollArea):
    """横向胶囊跳转条。

    旧版设置页是「左侧 10 项子导航 + 右侧 10 个堆叠页」，切换即换页，
    看不到全貌。新版设置是一份连续滚动的长文档，胶囊条只做定位，
    滚动时也会自动回写当前小节。
    """

    indexChanged = Signal(int)

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        self.setObjectName("railscroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(tokens(current_mode())["rail_h"])
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        inner = QWidget()
        inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(inner)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(6)
        row.setAlignment(Qt.AlignVCenter)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = []
        for i, label in enumerate(labels):
            btn = QPushButton(label)
            btn.setProperty("class", "chip")
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, idx=i: self.setCurrentIndex(idx))
            row.addWidget(btn)
            self._group.addButton(btn, i)
            self._buttons.append(btn)
        self.setWidget(inner)
        if self._buttons:
            self.setCurrentIndex(0)

    def count(self) -> int:
        return len(self._buttons)

    def currentIndex(self) -> int:
        return self._group.checkedId()

    def button(self, i: int) -> QPushButton:
        return self._buttons[i] if 0 <= i < len(self._buttons) else None

    def setCurrentIndex(self, index: int):
        if not (0 <= index < len(self._buttons)):
            return
        btn = self._buttons[index]
        if self.currentIndex() == index and btn.isChecked():
            return
        btn.setChecked(True)
        self.ensureWidgetVisible(btn)
        self.indexChanged.emit(index)

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), tokens(current_mode())["rail_h"])


# ------------------------------------------------------------- 指标卡

class MetricTile(QFrame):
    """指标卡：大数字 + 名称 + 一行补充说明。

    页面顶部用 3~4 个指标卡回答「现在什么状态」，用户不进子页就能判断
    要不要动手（旧版这些数字散在表格列里，要扫一遍才知道）。
    """

    def __init__(self, label: str, caption: str = "", parent=None):
        super().__init__(parent)
        self.setProperty("class", "tile")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(2)

        self.value_label = QLabel("-")
        self.value_label.setProperty("class", "metric")
        v.addWidget(self.value_label)

        self.name_label = QLabel(label)
        self.name_label.setProperty("class", "hint")
        v.addWidget(self.name_label)

        self.caption_label = QLabel(caption)
        self.caption_label.setProperty("class", "hint")
        self.caption_label.setVisible(bool(caption))
        v.addWidget(self.caption_label)

    def set_value(self, text: str, tone: str = ""):
        """tone: '' / 'ok' / 'warn' / 'err'。"""
        self.value_label.setText(text)
        _polish(self.value_label, f"metric-{tone}" if tone else "metric")

    def set_caption(self, text: str):
        self.caption_label.setText(text)
        self.caption_label.setVisible(bool(text))


# ----------------------------------------------------------- 日志抽屉

class LogDrawer(QWidget):
    """底部日志抽屉。

    旧版「运行日志」是任务页里一张固定高度的卡片，人在别的页面时看不到
    进度；榜单日志又是另一张卡片，两份日志分散。现在统一成一个抽屉：
    任务一开始自动展开，随时可收起，用一个小分段控件切换日志源。
    """

    def __init__(self, sources, parent=None):
        """sources: [标签名]，每个标签一个只读日志框。"""
        super().__init__(parent)
        self.setProperty("class", "drawer")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 8, 16, 10)
        v.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.tabs = SegmentedControl(sources)
        self.tabs.setFixedHeight(30)
        head.addWidget(self.tabs)
        head.addStretch(1)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setProperty("class", "ghost")
        head.addWidget(self.btn_clear)
        v.addLayout(head)

        self.stack = QStackedWidget()
        self.views = []
        for _name in sources:
            view = QPlainTextEdit()
            view.setProperty("class", "log")
            view.setReadOnly(True)
            view.setMaximumBlockCount(2000)
            view.setPlaceholderText("这里显示任务运行过程。")
            self.stack.addWidget(view)
            self.views.append(view)
        self.tabs.indexChanged.connect(self.stack.setCurrentIndex)
        v.addWidget(self.stack, 1)

        self._h = tokens(current_mode())["drawer_h"]
        self._anim = QPropertyAnimation(self, b"maximumHeight", self)
        self._anim.setDuration(MOTION["drawer"])
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._expanded = True
        QTimer.singleShot(0, lambda: self.set_expanded(False, animate=False))

    def view(self, i: int) -> QPlainTextEdit:
        return self.views[i] if 0 <= i < len(self.views) else None

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, on: bool, animate: bool = True):
        self._expanded = on
        target = self._h if on else 0
        self.stack.setVisible(on)
        if not animate:
            self.setMaximumHeight(target)
            self.setMinimumHeight(0)
            self.setVisible(on)
            return
        self.setVisible(True)
        self._anim.stop()
        self._anim.setStartValue(self.maximumHeight())
        self._anim.setEndValue(target)
        self._anim.start()

    def toggle(self):
        self.set_expanded(not self._expanded)
