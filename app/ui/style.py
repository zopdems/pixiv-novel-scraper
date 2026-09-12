ACCENT = "#0096fa"
ACCENT_DARK = "#0077cc"
BG = "#f5f6f8"
CARD = "#ffffff"
BORDER = "#dcdfe4"
TEXT = "#2b2f36"
TEXT_DIM = "#8a919c"
LOG_BG = "#22242b"
LOG_FG = "#d6d9e0"

QSS = f"""
* {{
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13px;
    color: {TEXT};
    outline: none;
}}

QMainWindow, QDialog, QWidget {{
    background: {BG};
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 7px 16px;
    min-height: 18px;
}}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:pressed {{ background: #eef6ff; }}
QPushButton:disabled {{ color: {TEXT_DIM}; background: {BG}; border-color: {BG}; }}

QPushButton[class="primary"] {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton[class="primary"]:hover {{ background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; }}
QPushButton[class="primary"]:pressed {{ background: #0060aa; }}
QPushButton[class="primary"]:disabled {{ background: #b9dcf5; border-color: #b9dcf5; }}

QPushButton[class="danger"]:hover {{ border-color: #e5484d; color: #e5484d; }}

/* ---------- 输入控件 ---------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QPlainTextEdit {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QTimeEdit:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    selection-background-color: #eef6ff;
    selection-color: {TEXT};
    padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QTimeEdit::up-button, QTimeEdit::down-button {{
    border: none; width: 20px;
}}

/* ---------- 选项卡 ---------- */
QTabWidget::pane {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 9px 22px;
    margin-right: 4px;
    color: {TEXT_DIM};
}}
QTabBar::tab:hover {{ color: {ACCENT}; }}
QTabBar::tab:selected {{
    color: {ACCENT};
    font-weight: 600;
    border-bottom: 2px solid {ACCENT};
}}

/* ---------- 表格 ---------- */
QTableWidget {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: #f0f1f4;
    alternate-background-color: #fafbfc;
}}
QTableWidget::item {{ padding: 6px 8px; border: none; }}
QTableWidget::item:selected {{ background: #e3f1ff; color: {TEXT}; }}
QHeaderView::section {{
    background: {CARD};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px;
    color: {TEXT_DIM};
    font-weight: 600;
}}

/* ---------- 日志视图 ---------- */
QPlainTextEdit[class="log"] {{
    background: {LOG_BG};
    color: {LOG_FG};
    border: none;
    border-radius: 8px;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
    padding: 10px;
}}

/* ---------- 复选框 ---------- */
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background: {CARD};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: none;
}}
QCheckBox::indicator:checked:hover {{ background: {ACCENT_DARK}; }}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #c9cdd4; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {TEXT_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #c9cdd4; border-radius: 5px; min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---------- 其他 ---------- */
QLabel[class="hint"] {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel[class="status-ok"] {{ color: #18a058; font-weight: 600; }}
QLabel[class="status-err"] {{ color: #e5484d; font-weight: 600; }}

QToolTip {{
    background: {TEXT}; color: #ffffff;
    border: none; border-radius: 4px; padding: 6px 8px;
}}

QMessageBox QPushButton {{ min-width: 72px; }}
"""
