"""视觉主题：设计令牌 + QPalette + QSS 生成器。

提供浅色 / 深色两套配色，QSS 由模板 + 令牌渲染，切换主题时整体重建即可。
对外接口：
    LIGHT / DARK              设计令牌字典
    tokens(mode)              取令牌
    build_qss(mode)           生成样式表
    build_palette(mode)       生成 QPalette
    apply_theme(app, mode)    一次性应用（含系统跟随）
    resolve_mode(mode)        'auto' -> 'light' / 'dark'
    current_mode()            当前生效模式
"""

import string

from PySide6.QtGui import QColor, QPalette

from .icons import asset

# ------------------------------------------------------------ 设计令牌

LIGHT = {
    "mode": "light",
    # 背景层级
    "canvas": "#F1F4F9",        # 窗口底色
    "sidebar": "#FFFFFF",       # 侧边导航
    "surface": "#FFFFFF",       # 卡片
    "surface_alt": "#F6F8FC",   # 卡片内浅底 / 表头 / 斑马纹
    "surface_hover": "#EEF2F8", # 悬停
    # 描边
    "border": "#E4E9F0",
    "border_strong": "#D2DAE5",
    "separator": "#EDF1F6",
    # 文字
    "text": "#1B2430",
    "text_dim": "#5E6B80",
    "text_muted": "#98A3B5",
    # 主色（Pixiv 蓝）
    "accent": "#0096FA",
    "accent_hover": "#0A87E0",
    "accent_press": "#0776C6",
    "accent_soft": "#E4F2FF",
    "accent_soft_border": "#BEE1FF",
    "accent_text": "#0072C6",
    "on_accent": "#FFFFFF",
    # 语义色
    "success": "#16A34A",
    "success_soft": "#E6F7EE",
    "warn": "#D97A06",
    "warn_soft": "#FDF2E2",
    "danger": "#E0453E",
    "danger_soft": "#FDECEB",
    # 日志终端
    "log_bg": "#181D26",
    "log_fg": "#C3D0E0",
    "log_dim": "#7B8AA0",
    # 其它
    "scroll": "#C9D2DD",
    "scroll_hover": "#9DA9BA",
    "tip_bg": "#1B2430",
    "tip_fg": "#FFFFFF",
    "shadow": "rgba(23, 43, 77, 0.10)",
}

DARK = {
    "mode": "dark",
    "canvas": "#12151B",
    "sidebar": "#191D25",
    "surface": "#1D222B",
    "surface_alt": "#232935",
    "surface_hover": "#2A313D",
    "border": "#2B323D",
    "border_strong": "#3A434F",
    "separator": "#242A34",
    "text": "#E6EAF1",
    "text_dim": "#9BA7B8",
    "text_muted": "#6C7789",
    "accent": "#3FB4FF",
    "accent_hover": "#63C3FF",
    "accent_press": "#2299E8",
    "accent_soft": "#123349",
    "accent_soft_border": "#1E4E70",
    "accent_text": "#7FCDF7",
    "on_accent": "#08131C",
    "success": "#3FCF8E",
    "success_soft": "#12331F",
    "warn": "#F5A524",
    "warn_soft": "#33260E",
    "danger": "#FF6B63",
    "danger_soft": "#3A1F1E",
    "log_bg": "#0D1015",
    "log_fg": "#A6B3C5",
    "log_dim": "#67737F",
    "scroll": "#39414E",
    "scroll_hover": "#4E5867",
    "tip_bg": "#2A313D",
    "tip_fg": "#E6EAF1",
    "shadow": "rgba(0, 0, 0, 0.35)",
}

_CURRENT = {"mode": "light"}


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

def build_palette(mode: str = "light") -> QPalette:
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
    border: none;
    border-radius: 6px;
    padding: 6px 9px;
}

/* ============================ 容器 ============================ */
QFrame[class="card"] {
    background: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QFrame[class="soft"] {
    background: $surface_alt;
    border: none;
    border-radius: 10px;
}
QFrame#topbar {
    background: $surface;
    border: none;
    border-bottom: 1px solid $border;
}
QFrame#sidebar {
    background: $sidebar;
    border: 1px solid $border;
    border-radius: 12px;
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

/* 状态徽标 */
QLabel[class="badge"] {
    background: $accent_soft;
    color: $accent_text;
    border: 1px solid $accent_soft_border;
    border-radius: 10px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: 600;
}
QLabel[class="badge-idle"] {
    background: $surface_alt;
    color: $text_dim;
    border: 1px solid $border;
    border-radius: 10px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: 600;
}
QLabel[class="badge-ok"] {
    background: $success_soft;
    color: $success;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: 600;
}
QLabel[class="badge-warn"] {
    background: $warn_soft;
    color: $warn;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: 600;
}
QLabel[class="badge-err"] {
    background: $danger_soft;
    color: $danger;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: 600;
}

/* ============================ 按钮 ============================ */
QPushButton {
    background: $surface;
    border: 1px solid $border_strong;
    border-radius: 8px;
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
    border-radius: 8px;
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
    border-radius: 10px;
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
    border-radius: 8px;
    selection-background-color: $accent_soft;
    selection-color: $accent_text;
    padding: 4px;
}
QComboBox QAbstractItemView::item {
    min-height: 28px;
    padding: 4px 9px;
    border-radius: 6px;
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
    border-radius: 5px;
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

/* ========================== 侧边导航 ========================== */
QListWidget#nav, QListWidget#subnav {
    background: transparent;
    border: none;
    padding: 6px;
    outline: 0;
}
QListWidget#nav::item {
    height: 38px;
    padding-left: 8px;
    margin: 2px 4px;
    border-radius: 9px;
    color: $text_dim;
}
QListWidget#nav::item:hover       { background: $surface_alt; color: $text; }
QListWidget#nav::item:selected    {
    background: $accent_soft;
    color: $accent_text;
    font-weight: 600;
}
QListWidget#nav::item:selected:hover { background: $accent_soft; color: $accent_text; }

QListWidget#subnav::item {
    height: 34px;
    padding-left: 8px;
    margin: 2px 4px;
    border-radius: 8px;
    color: $text_dim;
    font-size: 12.5px;
}
QListWidget#subnav::item:hover    { background: $surface_alt; color: $text; }
QListWidget#subnav::item:selected {
    background: $accent_soft;
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
    border-radius: 8px;
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
QTableWidget, QTableView {
    background: $surface;
    alternate-background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: 10px;
    gridline-color: $separator;
    selection-background-color: $accent_soft;
    selection-color: $accent_text;
}
QTableWidget::item, QTableView::item {
    padding: 7px 10px;
    border: none;
}
QTableWidget::item:selected, QTableView::item:selected {
    background: $accent_soft;
    color: $accent_text;
}
QTableWidget::item:hover, QTableView::item:hover { background: $surface_hover; }
QHeaderView::section {
    background: $surface_alt;
    color: $text_dim;
    border: none;
    border-bottom: 1px solid $border;
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
    border-bottom: 1px solid $border;
}

/* ============================ 日志 ============================ */
QPlainTextEdit[class="log"] {
    background: $log_bg;
    color: $log_fg;
    border: 1px solid $border;
    border-radius: 10px;
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
    border-radius: 3px;
    height: 5px;
    text-align: center;
}
QProgressBar::chunk {
    background: $accent;
    border-radius: 3px;
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
    border-radius: 5px;
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
    border-radius: 5px;
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
    border-radius: 12px;
    margin-top: 14px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: $text;
}

/* ========================== 菜单 ========================== */
QMenu {
    background: $surface;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 5px;
}
QMenu::item {
    padding: 7px 22px 7px 12px;
    border-radius: 6px;
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
    border: none;
    border-radius: 14px;
}

/* ========================== 其它 ========================== */
QMessageBox QPushButton { min-width: 78px; }
QSplitter::handle { background: $separator; }
"""


def build_qss(mode: str = "light") -> str:
    """按给定模式渲染完整样式表。"""
    t = dict(tokens(mode))
    t["check_white"] = asset("check", t["on_accent"])
    t["up_arrow"] = asset("up", t["text_dim"])
    t["down_arrow"] = asset("down", t["text_dim"])
    return string.Template(_QSS).safe_substitute(**t)


def apply_theme(app, mode: str = "light") -> str:
    """把主题应用到整个 QApplication，返回实际生效的模式。"""
    resolved = resolve_mode(mode)
    _CURRENT["mode"] = resolved
    app.setPalette(build_palette(resolved))
    app.setStyleSheet(build_qss(resolved))
    return resolved
