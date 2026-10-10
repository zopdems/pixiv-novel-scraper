from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .icons import app_icon, icon
from .theme import current_mode, tokens


class TrayIcon(QSystemTrayIcon):
    def __init__(self, window, on_fetch, on_quit, parent=None):
        super().__init__(app_icon(64), parent)
        self.window = window

        menu = QMenu()
        menu.setObjectName("trayMenu")
        act_fetch = menu.addAction("立即抓取")
        act_fetch.setIcon(icon("play", tokens()["accent_text"], 15))
        act_fetch.triggered.connect(on_fetch)
        act_open = menu.addAction("打开主窗口")
        act_open.setIcon(icon("external", tokens()["accent_text"], 15))
        act_open.triggered.connect(self._show_window)
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_quit.setIcon(icon("power", tokens()["danger"], 15))
        act_quit.triggered.connect(on_quit)
        self.setContextMenu(menu)

        self.activated.connect(self._on_activated)
        self.setToolTip("Pixiv 小说抓取器")

    def _show_window(self):
        self.window.showNormal()
        self.window.activateWindow()
        self.window.raise_()

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.window.showNormal()
            self.window.activateWindow()
            self.window.raise_()

    def refresh_icons(self):
        """主题切换后重建菜单图标颜色。"""
        menu = self.contextMenu()
        if not menu:
            return
        t = tokens(current_mode())
        for action in menu.actions():
            if action.isSeparator():
                continue
            name = {"立即抓取": "play", "打开主窗口": "external", "退出": "power"}.get(action.text())
            if name:
                color = t["danger"] if name == "power" else t["accent_text"]
                action.setIcon(icon(name, color, 15))

    def notify(self, title: str, message: str):
        self.showMessage(title, message, QSystemTrayIcon.Information, 8000)
