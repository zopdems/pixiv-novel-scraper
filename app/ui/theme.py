"""视觉主题：设计令牌 + QPalette + QSS 生成器。

这一版是「彻底重构」后的视觉基线，与旧版的区别：

* 配色：主色从 Pixiv 蓝改为靛蓝（indigo），中性色改为冷灰石墨，
  语义色重新配过一整套，浅色 / 深色两套都不再沿用旧色值。
* 层级：不再用「浮起的圆角卡片 + 描边」堆层级，改为
  画布 → 内容面（full-bleed）→ 浅底块（subtle）三级，分区用 1px 发丝线。
* 尺寸：4px 基准栅格（4/8/12/16/24/32），字号 11/12/13/14/20/24 六级，
  圆角 6/10/14 三级，全部集中在 METRICS / 令牌里，界面代码不写死数字。
* 动效：时长写在 MOTION 里，界面代码取同一份数值。

对外接口：
    LIGHT / DARK / METRICS / MOTION   设计令牌字典
    tokens(mode)                      取令牌
    build_qss(mode)                   生成样式表
    build_palette(mode)               生成 QPalette
    apply_theme(app, mode)            一次性应用（含系统跟随）
    resolve_mode(mode)                'auto' -> 'light' / 'dark'
    current_mode()                    当前生效模式
"""

import string

from PySide6.QtGui import QColor, QPalette

from .icons import asset

# ------------------------------------------------------------ 设计令牌

# 尺寸 / 间距 / 字号令牌：与配色无关，两套主题共用。
METRICS = {
    # 间距：4px 基准栅格
    "sp_1": 4,
    "sp_2": 8,
    "sp_3": 12,
    "sp_4": 16,
    "sp_5": 24,
    "sp_6": 32,
    # 兼容旧键名（历史调用还在用，语义等同 sp_1~sp_4）
    "sp_xs": 4,
    "sp_sm": 8,
    "sp_md": 12,
    "sp_lg": 16,
    # 区块内边距（浅底块 / 发丝线分区共用一套）
    "card_pad": 12,
    "card_gap": 8,
    "nav_pad": 8,
    "nav_gap": 4,
    # 尺寸
    "topbar_h": 58,     # 顶部命令栏
    "rail_h": 46,       # 设置页小节跳转条
    "dock_h": 34,       # 底部状态栏
    "row_h": 36,        # 表格行高（所有表格统一）
    "drawer_h": 200,    # 底部日志抽屉展开高度
    "edit_area_h": 84,  # 多行输入框（Cookie 等）
    # 旧键保留：新版不再有侧边栏，但其它模块可能还在读
    "sidebar_w": 186,
    "subnav_w": 172,
    # 字号
    "fs_micro": 11,     # 表头、角标
    "fs_caption": 12,   # 说明、次要信息
    "fs_body": 13,      # 正文
    "fs_subtitle": 14,  # 小标题
    "fs_brand": 14,     # 命令栏品牌名
    "fs_title": 20,     # 页标题
    "fs_metric": 24,    # 指标数字
    # 兼容旧键名
    "fs_hint": 12,
    "fs_section": 13,
    # 圆角
    "r_sm": 6,
    "r_md": 10,
    "r_lg": 14,
}

# 动效时长（毫秒）与曲线：界面代码从这里取，避免各处写死
MOTION = {
    "fast": 120,    # 悬停 / 颜色
    "base": 180,    # 分段控件指示器、按钮
    "page": 160,    # 页面切换淡入
    "drawer": 220,  # 日志抽屉展开 / 收起
}

LIGHT = {
    "mode": "light",
    # 背景层级：画布 → 内容面 → 浅底块
    "canvas": "#F3F4F7",        # 窗口底色（抽屉、间隙）
    "surface": "#FFFFFF",       # 内容面（全幅，无描边）
    "surface_alt": "#F9FAFC",   # 斑马纹 / 表头
    "surface_hover": "#EFF1F6", # 悬停
    "subtle": "#F1F2F7",        # 浅底块（指标卡、工具条底）
    "sidebar": "#FBFCFD",       # 底部状态栏
    # 描边
    "border": "#E2E5EC",
    "border_strong": "#CBD0DC",
    "separator": "#ECEEF3",     # 发丝线
    # 文字
    "text": "#141821",
    "text_dim": "#565E70",
    "text_muted": "#8B92A3",
    # 主色（靛蓝）
    "accent": "#4F46E5",
    "accent_hover": "#4338CA",
    "accent_press": "#3730A3",
    "accent_soft": "#EEEDFE",
    "accent_soft_border": "#D5D3FB",
    "accent_text": "#463FD1",
    "on_accent": "#FFFFFF",
    # 语义色
    "success": "#15803D",
    "success_soft": "#E7F6ED",
    "warn": "#B45309",
    "warn_soft": "#FDF3E3",
    "danger": "#DC2626",
    "danger_soft": "#FDECEC",
    # 日志终端
    "log_bg": "#141821",
    "log_fg": "#C9CFDB",
    "log_dim": "#767E90",
    # 其它
    "scroll": "#C6CBD6",
    "scroll_hover": "#9BA2B2",
    "tip_bg": "#141821",
    "tip_fg": "#FFFFFF",
    "shadow": "rgba(20, 28, 40, 0.10)",
    **METRICS,
    **MOTION,
}

DARK = {
    "mode": "dark",
    "canvas": "#0B0D12",
    "surface": "#14171E",
    "surface_alt": "#191D26",
    "surface_hover": "#1F242E",
    "subtle": "#1B1F28",
    "sidebar": "#12151B",
    "border": "#242832",
    "border_strong": "#333844",
    "separator": "#1D212A",
    "text": "#E8EAF0",
    "text_dim": "#98A0AF",
    "text_muted": "#6B7484",
    "accent": "#8B85F8",
    "accent_hover": "#A29DFA",
    "accent_press": "#7169EE",
    "accent_soft": "#1E1B3A",
    "accent_soft_border": "#322D60",
    "accent_text": "#A9A4FF",
    "on_accent": "#0B0A18",
    "success": "#3DD68C",
    "success_soft": "#11291D",
    "warn": "#F0B357",
    "warn_soft": "#2E2411",
    "danger": "#FF6B6B",
    "danger_soft": "#2E1717",
    "log_bg": "#080A0E",
    "log_fg": "#A9B1C1",
    "log_dim": "#5F6875",
    "scroll": "#333944",
    "scroll_hover": "#454D5A",
    "tip_bg": "#1E222B",
    "tip_fg": "#E8EAF0",
    "shadow": "rgba(0, 0, 0, 0.45)",
    **METRICS,
    **MOTION,
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

/* ======================= 骨架：命令栏 / 内容 / 停靠栏 =======================
   全幅平面（full-bleed）：内容区不描边、不浮起，分区一律用 1px 发丝线。 */
QFrame#commandbar {
    background: $surface;
    border: none;
    border-bottom: 1px solid $separator;
}
QFrame#content {
    background: $surface;
    border: none;
}
QFrame#rail {
    background: $surface;
    border: none;
    border-bottom: 1px solid $separator;
}
QFrame#dock {
    background: $sidebar;
    border: none;
    border-top: 1px solid $separator;
}
QFrame#drawer {
    background: $canvas;
    border: none;
    border-top: 1px solid $separator;
}

/* ============================ 容器 ============================ */
/* 浅底块：指标卡 / 工具条底 / 提示区，靠背景区分层级而不是靠描边 */
QFrame[class="panel"], QFrame[class="card"], QFrame[class="tile"] {
    background: $subtle;
    border: 1px solid $border;
    border-radius: 10px;
}
QFrame[class="soft"] {
    background: $subtle;
    border: none;
    border-radius: 10px;
}
/* 1px 发丝线 */
QFrame[class="rule"], QLabel[class="rule"] {
    background: $separator;
    border: none;
    min-height: 1px;
    max-height: 1px;
}

/* ============================ 文字 ============================ */
QLabel[class="page-title"] {
    font-size: 20px;
    font-weight: 700;
    color: $text;
    background: transparent;
}
QLabel[class="page-sub"] {
    font-size: 12px;
    color: $text_dim;
    background: transparent;
}
QLabel[class="group-title"] {
    font-size: 14px;
    font-weight: 600;
    color: $text;
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

/* 命令栏品牌标题 */
QLabel[class="brand"] {
    font-size: 14px;
    font-weight: 700;
    color: $text;
    background: transparent;
}
/* 浅底说明块（QLabel 版，与 QFrame[class="soft"] 同款） */
QLabel[class="soft"] {
    background: $subtle;
    color: $text;
    border: none;
    border-radius: 10px;
    padding: 12px 14px;
}
/* 向导步骤序号徽标 */
QLabel[class="step-num"] {
    background: $accent_soft;
    color: $accent_text;
    border: none;
    border-radius: 17px;
    font-size: 14px;
    font-weight: 700;
}

/* 指标数字：大字号 + 语义色，只在指标卡里用 */
QLabel[class="metric"] {
    font-size: 24px;
    font-weight: 700;
    color: $text;
    background: transparent;
}
QLabel[class="metric-ok"]   { font-size: 24px; font-weight: 700; color: $success; background: transparent; }
QLabel[class="metric-warn"] { font-size: 24px; font-weight: 700; color: $warn;    background: transparent; }
QLabel[class="metric-err"]  { font-size: 24px; font-weight: 700; color: $danger;  background: transparent; }

/* 状态徽标：形状只定义一次，下面各变体只区分配色 */
QLabel[class="badge"], QLabel[class="badge-idle"], QLabel[class="badge-ok"],
QLabel[class="badge-warn"], QLabel[class="badge-err"] {
    border-radius: 10px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: 600;
}
QLabel[class="badge"] {
    background: $accent_soft;
    color: $accent_text;
    border: 1px solid $accent_soft_border;
}
QLabel[class="badge-idle"] {
    background: $subtle;
    color: $text_dim;
    border: 1px solid $border;
}
QLabel[class="badge-ok"] {
    background: $success_soft;
    color: $success;
    border: 1px solid transparent;
}
QLabel[class="badge-warn"] {
    background: $warn_soft;
    color: $warn;
    border: 1px solid transparent;
}
QLabel[class="badge-err"] {
    background: $danger_soft;
    color: $danger;
    border: 1px solid transparent;
}

/* 状态圆点：8px，跟着语义色走 */
QLabel[class="dot"], QLabel[class="dot-idle"], QLabel[class="dot-ok"],
QLabel[class="dot-warn"], QLabel[class="dot-err"], QLabel[class="dot-run"] {
    border: none;
    border-radius: 4px;
    min-width: 8px;
    max-width: 8px;
    min-height: 8px;
    max-height: 8px;
}
QLabel[class="dot"]      { background: $text_muted; }
QLabel[class="dot-idle"] { background: $text_muted; }
QLabel[class="dot-ok"]   { background: $success; }
QLabel[class="dot-warn"] { background: $warn; }
QLabel[class="dot-err"]  { background: $danger; }
QLabel[class="dot-run"]  { background: $accent; }

/* 底部状态栏文字 */
QLabel[class="dock"] { color: $text_dim; font-size: 12px; background: transparent; }
QLabel[class="dock-strong"] { color: $text; font-size: 12px; font-weight: 600; background: transparent; }

/* ====================== 分段控件（Segment） ======================
   容器是浅底块，选中态是一块可滑动的白色指示片（在 widgets.py 里做动画）。 */
QFrame[class="seg"] {
    background: $subtle;
    border: 1px solid $border;
    border-radius: 9px;
}
QFrame[class="seg-ind"] {
    background: $surface;
    border: 1px solid $border_strong;
    border-radius: 7px;
}
QPushButton[class="seg-btn"] {
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 6px 14px;
    color: $text_dim;
    font-weight: 500;
}
QPushButton[class="seg-btn"]:hover  { color: $text; }
QPushButton[class="seg-btn"]:checked { color: $accent_text; font-weight: 600; }
QPushButton[class="seg-btn"]:disabled { color: $text_muted; }

/* 小节跳转条（设置页）：胶囊 chip，选中用主色浅底 */
QPushButton[class="chip"] {
    background: transparent;
    border: 1px solid $border;
    border-radius: 11px;
    padding: 5px 13px;
    color: $text_dim;
    font-size: 12px;
}
QPushButton[class="chip"]:hover  { background: $subtle; color: $text; border-color: $border_strong; }
QPushButton[class="chip"]:checked {
    background: $accent_soft;
    border-color: $accent;
    color: $accent_text;
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
    background: $subtle;
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
    background: $subtle;
    border-color: $border;
    color: $text_muted;
}

QPushButton[class="danger"] { color: $danger; }
QPushButton[class="danger"]:hover {
    background: $danger_soft;
    border-color: $danger;
    color: $danger;
}

/* 次级按钮：浅底，不用描边抢焦点 */
QPushButton[class="subtle"] {
    background: $subtle;
    border: 1px solid transparent;
    color: $text;
}
QPushButton[class="subtle"]:hover { background: $surface_hover; border-color: $border_strong; }

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
    background: $subtle;
    color: $text_muted;
}
QLineEdit[class="search"] {
    border-radius: 9px;
    padding: 7px 13px;
    font-size: 13px;
}
/* 命令栏里的全局搜索：更窄，不抢主按钮的视觉权重 */
QLineEdit[class="globalsearch"] {
    border-radius: 9px;
    padding: 6px 12px;
    font-size: 12px;
    background: $subtle;
    border: 1px solid transparent;
}
QLineEdit[class="globalsearch"]:focus {
    background: $surface;
    border-color: $accent;
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
QCheckBox::indicator:disabled { background: $subtle; border-color: $border; }

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
QTabBar::tab:hover    { background: $subtle; color: $text; }
QTabBar::tab:selected {
    background: $accent;
    color: $on_accent;
    font-weight: 600;
}

/* ============================ 表格 ============================
   平面化：不描边、不浮起，靠斑马纹 + 表头发丝线分界。
   注意：这里只写 QTableWidget，不要再写 QTableView。
   QCalendarWidget 内部就是一个 QTableView，一旦给它加内边距，
   单元格太小，两位数日期（10~31）会显示不出来。 */
QTableWidget {
    background: transparent;
    alternate-background-color: $surface_alt;
    border: none;
    gridline-color: $separator;
    selection-background-color: $accent_soft;
    selection-color: $accent_text;
}
QTableWidget::item {
    padding: 8px 12px;
    border: none;
}
QTableWidget::item:selected {
    background: $accent_soft;
    color: $accent_text;
}
QTableWidget::item:hover { background: $surface_hover; }
QHeaderView::section {
    background: $surface;
    color: $text_muted;
    border: none;
    border-bottom: 1px solid $border;
    padding: 8px 12px;
    font-size: 11px;
    font-weight: 600;
}
QHeaderView::section:hover { color: $text_dim; }
QTableCornerButton::section {
    background: $surface;
    border: none;
    border-bottom: 1px solid $border;
}

/* 树形列表（已删除作品）：与表格同一套平面规则 */
QTreeWidget {
    background: transparent;
    alternate-background-color: $surface_alt;
    border: none;
    selection-background-color: $accent_soft;
    selection-color: $accent_text;
}
QTreeWidget::item { padding: 6px 8px; border: none; }
QTreeWidget::item:hover { background: $surface_hover; }
QTreeWidget::item:selected { background: $accent_soft; color: $accent_text; }
QTreeWidget::branch { background: transparent; }

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
/* 日历格子不能继承 QTableWidget::item 的内边距，否则两位数日期显示不全 */
QCalendarWidget QTableView::item, QCalendarWidget QAbstractItemView::item {
    padding: 0px;
}
QCalendarWidget QWidget#qt_calendar_navigationbar { background: $subtle; }
/* 导航栏里的年/月按钮和前后箭头必须显式指定，否则会用系统默认的蓝色渐变 */
QCalendarWidget QWidget#qt_calendar_navigationbar QToolButton {
    background: $subtle;
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
    border-radius: 6px;
    padding: 4px 8px;
}
QCalendarWidget QToolButton:hover { background: $surface_hover; }
QCalendarWidget QMenu { background: $surface; color: $text; }

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
    background: $subtle;
    border: none;
    border-radius: 3px;
    height: 4px;
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

/* 设置页的小节跳转条：横向滚动条压到最细，只在溢出时出现 */
QScrollArea#railscroll QScrollBar:horizontal { height: 6px; margin: 0px; }
QScrollArea#railscroll QScrollBar::handle:horizontal { min-width: 24px; border-radius: 3px; }

/* ========================== 状态栏 ========================== */
QStatusBar {
    background: $sidebar;
    border-top: 1px solid $separator;
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
