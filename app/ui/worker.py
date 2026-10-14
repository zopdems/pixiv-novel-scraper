from PySide6.QtCore import QThread, Signal

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
