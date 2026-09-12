import sys

from PySide6.QtCore import QProcess


def launch_browser_login(parent, on_done):
    args = ["--login"] if getattr(sys, "frozen", False) else ["-m", "app.login_helper"]
    proc = QProcess(parent)
    proc.finished.connect(lambda *_: on_done())
    proc.start(sys.executable, args)
    return proc
