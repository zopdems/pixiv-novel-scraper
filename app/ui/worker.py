from PySide6.QtCore import QThread, Signal

from ..core.ranking import RankingService
from ..core.scraper import ScraperEngine, rebuild_md_indexes
from ..credentials import load_cookie


class ScrapeWorker(QThread):
    progress = Signal(dict)
    finished_report = Signal(object)

    def __init__(self, settings, db, logger, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.db = db
        self.logger = logger
        self.engine = None

    def stop(self):
        if self.engine:
            self.engine.request_stop()

    def run(self):
        try:
            self.engine = ScraperEngine(self.settings, self.db, load_cookie(), self.logger,
                                        progress_cb=self.progress.emit)
            report = self.engine.run()
        except Exception as e:
            self.logger.error("任务执行异常：%s", e, exc_info=True)
            from ..core.scraper import RunReport
            report = RunReport(aborted_reason=f"任务异常：{e}")
        self.finished_report.emit(report)


class RankingWorker(QThread):
    """排行榜抓取 / 指定作品正文下载（独立任务，不跟随每日抓取）。"""

    progress = Signal(dict)
    finished_report = Signal(object)

    def __init__(self, settings, db, rank_db, logger, modes=None,
                 download_only: dict = None, date: str = None,
                 backfill_days: int = 0, source: str = "", parent=None):
        super().__init__(parent)
        self.settings = settings
        self.db = db
        self.rank_db = rank_db
        self.logger = logger
        self.modes = list(modes or [])
        self.download_only = download_only
        self.date = date
        self.backfill_days = int(backfill_days or 0)
        self.source = source or ""
        self.service = None

    def stop(self):
        if self.service:
            self.service.request_stop()

    def run(self):
        try:
            self.service = RankingService(
                self.settings, self.db, self.rank_db, load_cookie(), self.logger,
                progress_cb=self.progress.emit)
            if self.download_only:
                report = self.service.download_works(
                    self.download_only, source=self.source)
            elif self.backfill_days > 0:
                report = self.service.backfill(
                    self.modes, self.backfill_days,
                    download=bool(getattr(self.settings, "ranking_download", True)),
                    download_mode=str(getattr(self.settings, "ranking_download_mode", "new")),
                    source=self.source,
                )
            else:
                report = self.service.run(
                    self.modes,
                    download=bool(getattr(self.settings, "ranking_download", True)),
                    download_mode=str(getattr(self.settings, "ranking_download_mode", "new")),
                    date=self.date,
                    source=self.source,
                )
        except Exception as e:
            self.logger.error("榜单任务异常：%s", e, exc_info=True)
            report = {"modes": {}, "collected": 0, "targets": 0, "downloaded": 0,
                      "skipped": 0, "failed": 0, "errors": [f"任务异常：{e}"]}
        self.finished_report.emit(report)


class RebuildMdWorker(QThread):
    """离线重建全部作者的 Markdown 索引（不联网）。"""

    progress = Signal(int, int, str)
    finished_report = Signal(object)

    def __init__(self, settings, db, logger, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.db = db
        self.logger = logger
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            report = rebuild_md_indexes(self.settings, self.db, self.logger,
                                        progress_cb=lambda d, t, a: (
                                            None if self._stop else self.progress.emit(d, t, a)))
        except Exception as e:
            self.logger.error("索引重建异常：%s", e, exc_info=True)
            report = {"authors": 0, "written": 0, "failed": 0, "error": str(e)}
        self.finished_report.emit(report)
