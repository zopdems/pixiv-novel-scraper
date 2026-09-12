from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#12B886"))
    painter.setPen(QColor("#075E45"))
    painter.drawEllipse(4, 4, 56, 56)
    painter.setPen(QColor("white"))
    painter.drawText(pm.rect(), 0x84, "P")
    painter.end()
    return QIcon(pm)


class TrayIcon(QSystemTrayIcon):
    def __init__(self, window, on_fetch, on_quit, parent=None):
        super().__init__(make_icon(), parent)
        self.window = window
        menu = QMenu()
        act_fetch = menu.addAction("立即抓取")
        act_fetch.triggered.connect(on_fetch)
        act_open = menu.addAction("打开设置")
        act_open.triggered.connect(self._show_window)
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(on_quit)
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)
        self.setToolTip("Pixiv 小说自动抓取器")

    def _show_window(self):
        self.window.showNormal()
        self.window.activateWindow()
        self.window.select_settings_tab()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.window.showNormal()
            self.window.activateWindow()

    def notify(self, title: str, message: str):
        self.showMessage(title, message, QSystemTrayIcon.Information, 8000)
