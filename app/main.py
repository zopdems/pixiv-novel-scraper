import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from .autostart import set_autostart
from .config import load_settings, save_settings
from .core.db import Database
from .core.rank_db import SRC_SCHEDULED
from .credentials import load_cookie
from .logging_setup import setup_logging
from .scheduler import DailyScheduler
from .ui.icons import app_icon
from .ui.main_window import MainWindow
from .ui.onboarding import OnboardingWizard
from .ui.theme import apply_theme
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
    app.setWindowIcon(app_icon(64))

    logger = setup_logging()
    settings = load_settings()
    apply_theme(app, getattr(settings, "theme", "light"))
    db = Database(logger=logger)

    if settings.autostart and getattr(sys, "frozen", False):
        set_autostart(True)

    if not settings.onboarded:
        wizard = OnboardingWizard(settings, db)
        if wizard.exec() == QDialog.Accepted:
            settings.onboarded = True
            save_settings(settings)
        else:
            settings.onboarded = True
            save_settings(settings)

    window = MainWindow(settings, db, logger)
    scheduler = DailyScheduler(
        settings.fetch_time,
        int(getattr(settings, "catch_up_window_minutes", 0) or 0),
    )
    # 排行榜独立一个调度器，避免与主抓取共用一次触发（should_run 是一次性的）
    rank_scheduler = DailyScheduler(
        settings.fetch_time,
        int(getattr(settings, "catch_up_window_minutes", 0) or 0),
    )

    # 分批抓取的续抓状态：记录「上一轮因达到上限而截断」的完成时间，
    # 供定时 tick 判断间隔是否已到，从而自动接着抓下一批。
    batch_state = {"truncated_at": None}

    def notify_report(report):
        if getattr(report, "auth_expired", False):
            tray.notify("Cookie 已失效", "请重新导入登录 Cookie，否则定时抓取会持续失败")
        elif getattr(report, "aborted_reason", "") and not getattr(report, "truncated", False):
            tray.notify("抓取任务异常", report.aborted_reason)
        elif getattr(report, "truncated", False):
            batch_state["truncated_at"] = time.time()
            interval = int(getattr(settings, "batch_interval_minutes", 30) or 30)
            tray.notify(
                "分批抓取",
                f"本轮已下载 {getattr(report, 'new_count', 0) + getattr(report, 'updated_count', 0)} 部，"
                f"约 {interval} 分钟后自动续抓剩余作品",
            )
        else:
            batch_state["truncated_at"] = None
            tray.notify(
                "抓取完成",
                f"新增 {report.new_count} 部，更新 {report.updated_count} 部，失败 {report.failed_count} 部",
            )

    window.on_fetch_done = notify_report

    def notify_ranking(report):
        """排行榜任务的完成通知（定时抓取时通常人在别的页面，靠这个看到结果）。"""
        r = report or {}
        if r.get("auth_expired"):
            tray.notify("Cookie 已失效", "排行榜抓取中止，请重新导入登录 Cookie")
            return
        if r.get("errors") and not r.get("collected"):
            tray.notify("排行榜抓取未取到数据", str(r["errors"][0])[:80])
            return
        if r.get("days"):
            tray.notify("排行榜补抓完成",
                        f"{len(r['days'])} 天中 {r.get('days_ok', 0)} 天有数据，"
                        f"共收录 {r.get('collected', 0)} 条")
            return
        tray.notify("排行榜抓取完成",
                    f"收录 {r.get('collected', 0)} 条"
                    + (f"，新下载小说 {r.get('downloaded', 0)} 部"
                       if r.get("downloaded") else "，无新作品需下载"))

    window.on_ranking_done = notify_ranking

    tray = TrayIcon(window, on_fetch=window.start_fetch, on_quit=window.force_quit)
    tray.show()
    window.on_theme_changed = tray.refresh_icons

    def tick():
        scheduler.set_time(settings.fetch_time)
        scheduler.set_catch_up_window(int(getattr(settings, "catch_up_window_minutes", 0) or 0))

        # ---- 排行榜：独立开关、独立触发，不依赖作者名单与主抓取 ----
        if getattr(settings, "ranking_enabled", False):
            rank_scheduler.set_time(settings.fetch_time)
            rank_scheduler.set_catch_up_window(
                int(getattr(settings, "catch_up_window_minutes", 0) or 0))
            busy = (window.worker and window.worker.isRunning()) or \
                (window.rank_worker and window.rank_worker.isRunning())
            if not busy and rank_scheduler.should_run():
                logger.info("定时任务触发：抓取小说排行榜")
                # 定时任务抓「设置里勾选的所有榜单」，与页面手动抓取（单个榜）区分开
                window.fetch_ranking(use_all_modes=True, source=SRC_SCHEDULED)
            elif busy and rank_scheduler.pending_today():
                logger.info("排行榜定时任务等待：已有任务进行中，稍后空闲再触发")

        # ---- 分批续抓：上一轮因达到单轮上限而截断，间隔到了自动接着抓 ----
        if batch_state["truncated_at"] is not None:
            interval = int(getattr(settings, "batch_interval_minutes", 30) or 30)
            elapsed_min = (time.time() - batch_state["truncated_at"]) / 60.0
            if window.worker and window.worker.isRunning():
                pass  # 还在抓，不动
            elif elapsed_min >= interval:
                if not load_cookie():
                    tray.notify("无法续抓", "未配置登录 Cookie")
                    batch_state["truncated_at"] = None
                elif not db.list_authors(enabled_only=True):
                    logger.info("续抓跳过：作者列表为空")
                    batch_state["truncated_at"] = None
                else:
                    logger.info("分批续抓触发（距上一轮 %.0f 分钟）", elapsed_min)
                    batch_state["truncated_at"] = None  # 先清，避免本轮抓取期间重复触发
                    window.start_fetch()
            return  # 有续抓在排，本 tick 不再处理每日定时

        # 先判断「今天是否还有待跑的定时任务」但别消耗触发机会：
        # should_run() 一旦调用就记下今天已跑过，若后面发现缺 Cookie / 名单为空
        # 而 return，这次机会就被白白吃掉，用户补上 Cookie 后当天也不会再补跑。
        if not scheduler.pending_today():
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
            logger.info("定时任务跳过：已有任务进行中，稍后空闲再触发")
            return
        # 确认能真的开工，再消耗今天的触发机会
        if not scheduler.should_run():
            return
        logger.info("定时任务触发（%s）", settings.fetch_time)
        window.start_fetch()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(30 * 1000)

    if "--silent" in sys.argv:
        logger.info("静默启动：不显示主窗口，常驻系统托盘")
    else:
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
