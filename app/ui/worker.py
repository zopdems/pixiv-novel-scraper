from PySide6.QtCore import QThread, Signal

from ..core.scraper import ScraperEngine
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
