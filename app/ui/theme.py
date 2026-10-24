"""视觉主题：明日方舟（Arknights）工业风设计令牌 + QPalette + QSS 生成器。

视觉语言：
- 深色工业底 + 罗德岛「琥珀橙」强调色；
- 锐利小圆角（2px）替代圆润大圆角，硬朗描边分层，营造机兵面板质感；
- 选中态用「左侧强调条 + 主色文字」，而非整块高亮填充。

对外接口与旧版完全一致（tokens / build_qss / build_palette / apply_theme /
resolve_mode / current_mode），token 键名不变，既有代码零改动即可切换风格。
"""

import string

from PySide6.QtGui import QColor, QPalette

from .icons import asset

# ------------------------------------------------------------ 设计令牌

LIGHT = {
    "mode": "light",
    # 背景层级（明亮工业：米白底 + 硬朗灰层次）
    "canvas": "#E8E6E1",        # 窗口底色（暖灰米白）
    "sidebar": "#F2F0EB",       # 侧边导航
    "surface": "#FBFAF7",       # 卡片
    "surface_alt": "#EFEDE8",   # 卡片内浅底 / 表头 / 斑马纹
    "surface_hover": "#E3E0DA", # 悬停
    # 描边
    "border": "#D6D2CA",
    "border_strong": "#BDB8AF",
    "separator": "#E4E1DB",
    # 文字
    "text": "#23211E",
    "text_dim": "#5C5852",
    "text_muted": "#938E85",
    # 主色（罗德岛琥珀橙）
    "accent": "#A8640A",
    "accent_hover": "#8F5608",
    "accent_press": "#774607",
    "accent_soft": "#F3E4CD",
    "accent_soft_border": "#E0C79C",
    "accent_text": "#8A5508",
    "on_accent": "#FFFFFF",
    # 语义色
    "success": "#2E7D4F",
    "success_soft": "#DFEFE4",
    "warn": "#B06A08",
    "warn_soft": "#F7E9D4",
    "danger": "#B5433A",
    "danger_soft": "#F5DFDD",
    # 日志终端（始终深色，贴合工业终端）
    "log_bg": "#141416",
    "log_fg": "#D9D5CC",
    "log_dim": "#7E7A72",
    # 其它
    "scroll": "#BDB8AF",
    "scroll_hover": "#938E85",
    "tip_bg": "#23211E",
    "tip_fg": "#F5F3EF",
    "shadow": "rgba(40, 36, 28, 0.14)",
}

DARK = {
    "mode": "dark",
    # 背景层级（罗德岛主界面：近黑工业底）
    "canvas": "#0C0D10",        # 窗口底色
    "sidebar": "#111217",       # 侧边导航
    "surface": "#16171C",       # 卡片
    "surface_alt": "#1D1E24",   # 卡片内浅底 / 表头 / 斑马纹
    "surface_hover": "#26272F", # 悬停
    # 描边
    "border": "#2A2C34",
    "border_strong": "#3A3D47",
    "separator": "#212329",
    # 文字
    "text": "#EAEBF0",
    "text_dim": "#9BA0AB",
    "text_muted": "#686C76",
    # 主色（罗德岛琥珀橙）
    "accent": "#F2A33C",
    "accent_hover": "#F7B45C",
    "accent_press": "#CE8A2E",
    "accent_soft": "#2A2012",
    "accent_soft_border": "#4A3A1E",
    "accent_text": "#F5B65A",
    "on_accent": "#1C1408",
    # 语义色
    "success": "#4BBE7E",
    "success_soft": "#12291C",
    "warn": "#F2A33C",
    "warn_soft": "#2B2112",
    "danger": "#E5554A",
    "danger_soft": "#301A18",
    # 日志终端
    "log_bg": "#0A0B0E",
    "log_fg": "#C9CBD2",
    "log_dim": "#62656E",
    # 其它
    "scroll": "#33363F",
    "scroll_hover": "#4A4E59",
    "tip_bg": "#1D1E24",
    "tip_fg": "#EAEBF0",
    "shadow": "rgba(0, 0, 0, 0.5)",
}

_CURRENT = {"mode": "dark"}


def tokens(mode: str = None) -> dict:
    m = resolve_mode(mode or _CURRENT["mode"])
    return DARK if m == "dark" else LIGHT


def current_mode() -> str:
    return _CURRENT["mode"]


# ------------------------------------------------------- 主题模式解析

def system_prefers_light() -> bool:
    """读取 Windows 系统「应用颜色」偏好，读不到时按浅色处理。"""
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return bool(value)
    except Exception:
        return True


def resolve_mode(mode: str) -> str:
    if mode == "auto":
        return "light" if system_prefers_light() else "dark"
    return "dark" if mode == "dark" else "light"


# ------------------------------------------------------------- QPalette

def build_palette(mode: str = "dark") -> QPalette:
    t = tokens(mode)
    p = QPalette()
    p.setColor(QPalette.Window, QColor(t["canvas"]))
    p.setColor(QPalette.WindowText, QColor(t["text"]))
    p.setColor(QPalette.Base, QColor(t["surface"]))
    p.setColor(QPalette.AlternateBase, QColor(t["surface_alt"]))
    p.setColor(QPalette.Text, QColor(t["text"]))
    p.setColor(QPalette.Button, QColor(t["surface"]))
    p.setColor(QPalette.ButtonText, QColor(t["text"]))
    p.setColor(QPalette.Highlight, QColor(t["accent"]))
    p.setColor(QPalette.HighlightedText, QColor(t["on_accent"]))
    p.setColor(QPalette.Link, QColor(t["accent"]))
    p.setColor(QPalette.ToolTipBase, QColor(t["tip_bg"]))
    p.setColor(QPalette.ToolTipText, QColor(t["tip_fg"]))
    p.setColor(QPalette.PlaceholderText, QColor(t["text_muted"]))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(t["text_muted"]))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(t["text_muted"]))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t["text_muted"]))
    return p


# ------------------------------------------------------------------ QSS

_QSS = """
/* ============================ 基础 ============================ */
* {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI",
                 "PingFang SC", "Hiragino Sans GB", sans-serif;
    font-size: 13px;
    color: $text;
    outline: none;
}

QMainWindow, QDialog, QWizard, QMessageBox {
    background: $canvas;
}

QToolTip {
    background: $tip_bg;
    color: $tip_fg;
    border: 1px solid $border_strong;
    border-radius: 2px;
    padding: 6px 9px;
}

/* ============================ 容器 ============================ */
QFrame[class="card"] {
    background: $surface;
    border: 1px solid $border;
    border-radius: 2px;
}
QFrame[class="soft"] {
    background: $surface_alt;
    border: 1px solid $border;
    border-radius: 2px;
}
QFrame#topbar {
    background: $surface;
    border: none;
    border-bottom: 2px solid $accent_soft_border;
}
QFrame#sidebar {
    background: $sidebar;
    border: 1px solid $border;
    border-radius: 2px;
}

/* ============================ 文字 ============================ */
QLabel[class="page-title"] {
    font-size: 19px;
    font-weight: 700;
    color: $text;
    background: transparent;
}
QLabel[class="page-sub"] {
    font-size: 12px;
    color: $text_dim;
    background: transparent;
}
QLabel[class="section-title"] {
    font-size: 13px;
    font-weight: 600;
    color: $text;
    background: transparent;
}
QLabel[class="hint"] {
    color: $text_muted;
    font-size: 12px;
    background: transparent;
}
QLabel[class="status-ok"]  { color: $success; font-weight: 600; background: transparent; }
QLabel[class="status-err"] { color: $danger;  font-weight: 600; background: transparent; }

/* 状态徽标（锐利切角，明日方舟式标签） */
QLabel[class="badge"] {
    background: $accent_soft;
    color: $accent_text;
    border: 1px solid $accent_soft_border;
    border-radius: 2px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: 600;
}
QLabel[class="badge-idle"] {
    background: $surface_alt;
    color: $text_dim;
    border: 1px solid $border;
    border-radius: 2px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: 600;
}
QLabel[class="badge-ok"] {
    background: $success_soft;
    color: $success;
    border: 1px solid transparent;
    border-radius: 2px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: 600;
}
QLabel[class="badge-warn"] {
    background: $warn_soft;
    color: $warn;
    border: 1px solid transparent;
    border-radius: 2px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: 600;
}
QLabel[class="badge-err"] {
    background: $danger_soft;
    color: $danger;
    border: 1px solid transparent;
    border-radius: 2px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: 600;
}

/* ============================ 按钮 ============================ */
QPushButton {
    background: $surface;
    border: 1px solid $border_strong;
    border-radius: 2px;
    padding: 7px 16px;
    color: $text;
}
QPushButton:hover {
    background: $surface_hover;
    border-color: $accent;
    color: $accent_text;
}
QPushButton:pressed  { background: $accent_soft; }
QPushButton:disabled {
    background: $surface_alt;
    border-color: $border;
    color: $text_muted;
}

QPushButton[class="primary"] {
    background: $accent;
    border: 1px solid $accent;
    color: $on_accent;
    font-weight: 600;
}
QPushButton[class="primary"]:hover   { background: $accent_hover;  border-color: $accent_hover;  color: $on_accent; }
QPushButton[class="primary"]:pressed { background: $accent_press;  border-color: $accent_press;  color: $on_accent; }
QPushButton[class="primary"]:disabled {
    background: $surface_alt;
    border-color: $border;
    color: $text_muted;
}

QPushButton[class="danger"] { color: $danger; }
QPushButton[class="danger"]:hover {
    background: $danger_soft;
    border-color: $danger;
    color: $danger;
}

QPushButton[class="ghost"] {
    background: transparent;
    border: 1px solid transparent;
    color: $text_dim;
}
QPushButton[class="ghost"]:hover {
    background: $surface_hover;
    border-color: $border;
    color: $text;
}

QPushButton[class="icon"] {
    padding: 6px 8px;
    min-width: 30px;
}

/* ========================= 输入控件 ========================= */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QPlainTextEdit {
    background: $surface;
    border: 1px solid $border_strong;
    border-radius: 2px;
    padding: 7px 11px;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QTimeEdit:focus, QPlainTextEdit:focus {
    border-color: $accent;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled, QTimeEdit:disabled, QPlainTextEdit:disabled {
    background: $surface_alt;
    color: $text_muted;
}
QLineEdit[class="search"] {
    border-radius: 2px;
    padding: 9px 14px;
    font-size: 13px;
}

QComboBox { padding-right: 6px; }
QComboBox::drop-down {
    border: none;
    width: 24px;
    background: transparent;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid $text_dim;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background: $surface;
    border: 1px solid $border_strong;
    border-radius: 2px;
    selection-background-color: $accent_soft;
    selection-color: $accent_text;
    padding: 4px;
}
QComboBox QAbstractItemView::item {
    min-height: 28px;
    padding: 4px 9px;
    border-radius: 2px;
}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QTimeEdit::up-button, QTimeEdit::down-button {
    subcontrol-origin: border;
    width: 22px;
    border: none;
    background: transparent;
}
QSpinBox::up-button      { subcontrol-position: top right; }
QSpinBox::down-button    { subcontrol-position: bottom right; }
QDoubleSpinBox::up-button   { subcontrol-position: top right; }
QDoubleSpinBox::down-button { subcontrol-position: bottom right; }
QTimeEdit::up-button     { subcontrol-position: top right; }
QTimeEdit::down-button   { subcontrol-position: bottom right; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QTimeEdit::up-arrow {
    image: url("$up_arrow");
    width: 12px;
    height: 12px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QTimeEdit::down-arrow {
    image: url("$down_arrow");
    width: 12px;
    height: 12px;
}

/* ========================== 复选框 ========================== */
QCheckBox { spacing: 9px; background: transparent; }
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border: 1px solid $border_strong;
    border-radius: 2px;
    background: $surface;
}
QCheckBox::indicator:hover    { border-color: $accent; }
QCheckBox::indicator:checked  {
    background: $accent;
    border-color: $accent;
    image: url("$check_white");
}
QCheckBox::indicator:checked:hover { background: $accent_hover; border-color: $accent_hover; }
QCheckBox::indicator:disabled { background: $surface_alt; border-color: $border; }

/* ========================== 侧边导航 ==========================
   明日方舟选中态：左侧强调条 + 主色文字，而非整块填充。 */
QListWidget#nav, QListWidget#subnav {
    background: transparent;
    border: none;
    padding: 6px;
    outline: 0;
}
QListWidget#nav::item {
    height: 38px;
    padding-left: 10px;
    margin: 2px 4px;
    border-left: 2px solid transparent;
    border-radius: 0px;
    color: $text_dim;
}
QListWidget#nav::item:hover       { background: $surface_alt; color: $text; }
QListWidget#nav::item:selected    {
    background: $accent_soft;
    border-left: 2px solid $accent;
    color: $accent_text;
    font-weight: 600;
}
QListWidget#nav::item:selected:hover { background: $accent_soft; color: $accent_text; }

QListWidget#subnav::item {
    height: 34px;
    padding-left: 10px;
    margin: 2px 4px;
    border-left: 2px solid transparent;
    border-radius: 0px;
    color: $text_dim;
    font-size: 12.5px;
}
QListWidget#subnav::item:hover    { background: $surface_alt; color: $text; }
QListWidget#subnav::item:selected {
    background: $accent_soft;
    border-left: 2px solid $accent;
    color: $accent_text;
    font-weight: 600;
}
QListWidget#subnav::item:selected:hover { background: $accent_soft; color: $accent_text; }

/* ============================ 选项卡 ============================ */
QTabWidget::pane {
    background: transparent;
    border: none;
    top: 0px;
}
QTabBar {
    background: transparent;
    border: none;
}
QTabBar::tab {
    background: transparent;
    border: none;
    border-radius: 2px;
    padding: 7px 16px;
    margin-right: 4px;
    color: $text_dim;
}
QTabBar::tab:hover    { background: $surface_alt; color: $text; }
QTabBar::tab:selected {
    background: $accent;
    color: $on_accent;
    font-weight: 600;
}

/* ============================ 表格 ============================ */
/* 注意：这里只写 QTableWidget，不要再写 QTableView。
   QCalendarWidget 内部就是一个 QTableView，一旦给它加内边距，
   单元格太小，两位数日期（10~31）会显示不出来。 */
QTableWidget {
    background: $surface;
    alternate-background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: 2px;
    gridline-color: $separator;
    selection-background-color: $accent_soft;
    selection-color: $accent_text;
}
QTableWidget::item {
    padding: 7px 10px;
    border: none;
}
QTableWidget::item:selected {
    background: $accent_soft;
    color: $accent_text;
}
QTableWidget::item:hover { background: $surface_hover; }
QHeaderView::section {
    background: $surface_alt;
    color: $text_dim;
    border: none;
    border-bottom: 1px solid $border_strong;
    border-right: 1px solid $separator;
    padding: 9px 10px;
    font-size: 12px;
    font-weight: 600;
}
QHeaderView::section:last { border-right: none; }
QHeaderView::section:hover { color: $text; }
QTableCornerButton::section {
    background: $surface_alt;
    border: none;
    border-bottom: 1px solid $border_strong;
}

/* ========================== 日历（日期选择器） ==========================
   内部是 QTableView，必须显式接管配色，别让它继承上面表格那套规则。 */
QCalendarWidget QWidget {
    background: $surface;
    alternate-background-color: $surface;
}
QCalendarWidget QAbstractItemView:enabled {
    background: $surface;
    color: $text;
    selection-background-color: $accent_soft;
    selection-color: $accent_text;
}
QCalendarWidget QAbstractItemView:disabled {
    color: $text_muted;
}
QCalendarWidget QWidget#qt_calendar_navigationbar { background: $surface_alt; }
/* 导航栏里的年/月按钮和前后箭头必须显式指定，否则会用系统默认的蓝色渐变 */
QCalendarWidget QWidget#qt_calendar_navigationbar QToolButton {
    background: $surface_alt;
    color: $text;
}
QCalendarWidget QWidget#qt_calendar_navigationbar QSpinBox {
    background: $surface;
    color: $text;
}
QCalendarWidget QToolButton {
    color: $text;
    background: transparent;
    border: none;
    border-radius: 2px;
    padding: 4px 8px;
}
QCalendarWidget QToolButton:hover { background: $surface_hover; }
QCalendarWidget QMenu { background: $surface; color: $text; }

/* ============================ 日志 ============================ */
QPlainTextEdit[class="log"] {
    background: $log_bg;
    color: $log_fg;
    border: 1px solid $border;
    border-radius: 2px;
    font-family: "Cascadia Mono", "Cascadia Code", "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 12px;
    padding: 10px 12px;
    selection-background-color: $accent;
    selection-color: $on_accent;
}

/* ========================== 进度条 ========================== */
QProgressBar {
    background: $surface_alt;
    border: none;
    border-radius: 1px;
    height: 5px;
    text-align: center;
}
QProgressBar::chunk {
    background: $accent;
    border-radius: 1px;
}

/* ========================== 滚动条 ========================== */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 4px 2px;
    border: none;
}
QScrollBar::handle:vertical {
    background: $scroll;
    border-radius: 2px;
    min-height: 34px;
}
QScrollBar::handle:vertical:hover { background: $scroll_hover; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
    background: transparent;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px 4px;
    border: none;
}
QScrollBar::handle:horizontal {
    background: $scroll;
    border-radius: 2px;
    min-width: 34px;
}
QScrollBar::handle:horizontal:hover { background: $scroll_hover; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
    background: transparent;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

/* ========================== 状态栏 ========================== */
QStatusBar {
    background: $surface;
    border-top: 1px solid $border;
    color: $text_dim;
    font-size: 12px;
}
QStatusBar::item { border: none; }
QStatusBar QLabel { color: $text_dim; font-size: 12px; background: transparent; }

/* ========================== 分组框 ========================== */
QGroupBox {
    background: $surface;
    border: 1px solid $border;
    border-radius: 2px;
    margin-top: 14px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: $accent_text;
}

/* ========================== 菜单 ========================== */
QMenu {
    background: $surface;
    border: 1px solid $border_strong;
    border-radius: 2px;
    padding: 5px;
}
QMenu::item {
    padding: 7px 22px 7px 12px;
    border-radius: 2px;
    color: $text;
}
QMenu::item:selected { background: $accent_soft; color: $accent_text; }
QMenu::separator { height: 1px; background: $separator; margin: 4px 8px; }

/* ========================== 向导 ========================== */
QWizard #qt_wizard_titlelabel {
    font-size: 20px;
    font-weight: 700;
    color: $text;
}
QWizard #qt_wizard_subtitlelabel {
    font-size: 12.5px;
    color: $text_dim;
}
QWizard QPushButton { min-width: 82px; }
QWizard QPushButton:default, QMessageBox QPushButton:default {
    background: $accent;
    border: 1px solid $accent;
    color: $on_accent;
    font-weight: 600;
}
QWizard QPushButton:default:hover, QMessageBox QPushButton:default:hover {
    background: $accent_hover;
    border-color: $accent_hover;
    color: $on_accent;
}
QWizard QPushButton:default:pressed, QMessageBox QPushButton:default:pressed {
    background: $accent_press;
    border-color: $accent_press;
    color: $on_accent;
}
QFrame[class="step-badge"] {
    background: $accent_soft;
    border: 1px solid $accent_soft_border;
    border-radius: 2px;
}

/* ========================== 其它 ========================== */
QMessageBox QPushButton { min-width: 78px; }
QSplitter::handle { background: $separator; }
"""


def build_qss(mode: str = "dark") -> str:
    """按给定模式渲染完整样式表。"""
    t = dict(tokens(mode))
    t["check_white"] = asset("check", t["on_accent"])
    t["up_arrow"] = asset("up", t["text_dim"])
    t["down_arrow"] = asset("down", t["text_dim"])
    return string.Template(_QSS).safe_substitute(**t)


def apply_theme(app, mode: str = "dark") -> str:
    """把主题应用到整个 QApplication，返回实际生效的模式。"""
    resolved = resolve_mode(mode)
    _CURRENT["mode"] = resolved
    app.setPalette(build_palette(resolved))
    app.setStyleSheet(build_qss(resolved))
    return resolved
