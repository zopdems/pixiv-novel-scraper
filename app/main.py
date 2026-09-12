import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from .config import load_settings, save_settings
from .core.db import Database
from .credentials import load_cookie
from .logging_setup import setup_logging
from .scheduler import DailyScheduler
from .ui.main_window import MainWindow
from .ui.onboarding import OnboardingWizard
from .ui.tray import TrayIcon


def main():
    if "--login" in sys.argv:
        from .login_helper import run as login_run
        sys.exit(login_run())

    if "--selftest" in sys.argv:
        import os
        from .config import app_data_dir
        from .pixiv.client import PixivClient
        settings = load_settings()
        client = PixivClient(settings, load_cookie())
        ok, msg = client.test_connection()
        proxies = client._build_proxies(settings)
        client.close()
        out = os.path.join(app_data_dir(), "selftest.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"ok={ok}\nmsg={msg}\nproxy={proxies}\n")
        print(f"selftest: ok={ok} msg={msg} proxy={proxies}")
        sys.exit(0 if ok else 1)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    from .ui.style import QSS
    app.setStyleSheet(QSS)
    logger = setup_logging()
    settings = load_settings()
    db = Database(logger=logger)

    if not settings.onboarded:
        wizard = OnboardingWizard(settings, db)
        if wizard.exec() == QDialog.Accepted:
            settings.onboarded = True
            save_settings(settings)
        else:
            settings.onboarded = True
            save_settings(settings)

    window = MainWindow(settings, db, logger)
    scheduler = DailyScheduler(settings.fetch_time)

    def notify_report(report):
        if report.aborted_reason:
            tray.notify("抓取任务异常", report.aborted_reason)
        else:
            tray.notify(
                "抓取完成",
                f"新增 {report.new_count} 部，更新 {report.updated_count} 部，失败 {report.failed_count} 部",
            )

    window.on_fetch_done = notify_report

    tray = TrayIcon(window, on_fetch=window.start_fetch, on_quit=window.force_quit)
    tray.show()

    def tick():
        scheduler.set_time(settings.fetch_time)
        if not scheduler.should_run():
            return
        if not load_cookie():
            tray.notify("无法开始抓取", "未配置登录 Cookie，请在设置页导入")
            window.showNormal()
            window.select_settings_tab()
            return
        if not db.list_authors(enabled_only=True):
            logger.info("定时任务跳过：作者列表为空")
            return
        if window.worker and window.worker.isRunning():
            logger.info("定时任务跳过：已有任务进行中")
            return
        logger.info("定时任务触发（%s）", settings.fetch_time)
        window.start_fetch()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(30 * 1000)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
