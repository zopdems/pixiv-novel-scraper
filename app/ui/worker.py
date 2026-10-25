from PySide6.QtCore import QThread, Signal

from ..core.db import DISABLE_REASON_DEACTIVATED
from ..core.ranking import RankingService
from ..core.scraper import ScraperEngine, rebuild_md_indexes
from ..credentials import load_cookie
from ..pixiv import api
from ..pixiv.client import PixivClient


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


class SingleWorkWorker(QThread):
    """重新打包 / 检查更新单个作品（复用单篇管线，不联网拉作者列表）。"""

    finished_report = Signal(object)

    def __init__(self, settings, db, logger, kind: str, work_id: str, author_id: str,
                 parent=None):
        super().__init__(parent)
        self.settings = settings
        self.db = db
        self.logger = logger
        self.kind = kind          # 'novel' / 'illust'
        self.work_id = work_id
        self.author_id = author_id
        self.engine = None

    def run(self):
        try:
            engine = ScraperEngine(self.settings, self.db, load_cookie(),
                                   self.logger)
            self.engine = engine
            stat = {"new": 0, "updated": 0, "skipped": 0, "failed": 0}
            if self.kind == "novel":
                # 强制走更新判定（忽略时间戳），实现「重新打包」语义
                name = engine._process_novel(self.work_id, self.author_id, stat=stat)
            else:
                stat = {"illust_new": 0, "illust_updated": 0,
                        "illust_skipped": 0, "illust_failed": 0}
                engine._process_illust(self.work_id, self.author_id, stat)
                name = ""
            engine.client.close()
            self.finished_report.emit({
                "ok": stat.get("failed", 0) == 0 and stat.get("illust_failed", 0) == 0,
                "new": stat.get("new", 0), "updated": stat.get("updated", 0),
                "skipped": stat.get("skipped", 0), "failed": stat.get("failed", 0),
            })
        except Exception as e:
            self.logger.error("单个作品处理异常：%s", e, exc_info=True)
            if self.engine:
                self.engine.client.close()
            self.finished_report.emit({"ok": False, "error": str(e)})


class DryRunWorker(QThread):
    """试运行：走完整抓取流程但只记录「会下载/跳过」，不下载、不写库。"""

    finished_report = Signal(object)

    def __init__(self, settings, db, logger, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.db = db
        self.logger = logger

    def run(self):
        try:
            engine = ScraperEngine(self.settings, self.db, load_cookie(),
                                   self.logger)
            engine.dry_run = True
            report = engine.run()
            engine.client.close()
            self.finished_report.emit({
                "ok": True,
                "would_download": engine.dry_run_would_download,
                "new": report.new_count, "updated": report.updated_count,
                "skipped": report.skipped_count, "failed": report.failed_count,
                "auth_expired": report.auth_expired,
            })
        except Exception as e:
            self.logger.error("试运行异常：%s", e, exc_info=True)
            self.finished_report.emit({"ok": False, "error": str(e)})


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
            # 回调返回 False 让 rebuild_md_indexes 提前收尾：
            # 只让回调「不 emit」的话循环仍会跑完全部作者，点停止等于没用。
            def _cb(done, total, author_id):
                if self._stop:
                    return False
                self.progress.emit(done, total, author_id)
                return True

            report = rebuild_md_indexes(self.settings, self.db, self.logger, progress_cb=_cb)
            if self._stop:
                report["stopped"] = True
        except Exception as e:
            self.logger.error("索引重建异常：%s", e, exc_info=True)
            report = {"authors": 0, "written": 0, "failed": 0, "error": str(e)}
        self.finished_report.emit(report)


class AuthorCheckWorker(QThread):
    """逐个查询作者账号是否仍然存在（已注销 / 已离开 pixiv）。

    抓取流程为了省请求，只在「没昵称」或「远端零作品」时才查账号主体接口，
    已注销账号若作品列表仍有返回就会被漏判；这里是用户显式发起的检查，
    因此对每位目标都查一次，且查不出结论时记为 unknown，绝不默认当成账号正常。
    """

    progress = Signal(dict)
    finished_result = Signal(dict)

    def __init__(self, settings, db, logger, targets, parent=None):
        """targets: [(author_id, kind), ...]，kind 为 novel / illust。"""
        super().__init__(parent)
        self.settings = settings
        self.db = db
        self.logger = logger
        self.targets = list(targets)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        result = {"deactivated": [], "alive": [], "renamed": [], "unknown": [], "error": ""}
        try:
            cookie = load_cookie()
            if not cookie:
                result["error"] = "未配置 Cookie，无法查询账号状态。请先在设置里导入 Cookie。"
                self.finished_result.emit(result)
                return
            client = PixivClient(self.settings, cookie)
            try:
                total = len(self.targets)
                for i, (aid, kind) in enumerate(self.targets):
                    if self._stop:
                        break
                    self.progress.emit({"index": i + 1, "total": total, "author_id": aid})
                    try:
                        exists, name = api.get_user_status(client, aid)
                    except Exception as e:
                        # 网络 / 风控 / 解析失败都算「未确认」，不冒充「账号正常」
                        result["unknown"].append({"aid": aid, "kind": kind, "reason": str(e)})
                        continue
                    if not exists:
                        self.db.set_author_enabled(aid, False, kind=kind,
                                                   reason=DISABLE_REASON_DEACTIVATED)
                        result["deactivated"].append({"aid": aid, "kind": kind})
                        continue
                    if name:
                        cur = self.db.get_author(aid, kind=kind) or {}
                        if name != (cur.get("author_name") or ""):
                            self.db.update_author_name(aid, name, kind=kind)
                            result["renamed"].append(
                                {"aid": aid, "kind": kind,
                                 "old": cur.get("author_name") or "", "name": name})
                            continue
                    result["alive"].append({"aid": aid, "kind": kind})
            finally:
                client.close()
        except Exception as e:
            result["error"] = str(e)
        self.finished_result.emit(result)
