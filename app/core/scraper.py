import os
import shutil
import threading
import time
import traceback
from dataclasses import dataclass, field

from ..config import Settings
from ..pixiv import api
from ..pixiv.client import AuthExpiredError, PixivClient, PixivRequestError, RiskControlError
from ..pixiv.markup import convert_novel_markup
from . import epub_exporter, md_index, paths, utils
from .db import (DISABLE_REASON_DEACTIVATED, SERIES_RECHECK_DAYS, Database)
from .epub_exporter import build_epub
from .md_index import write_author_md, write_illust_md
from .models import Status


@dataclass
class RunReport:
    started_at: str = ""
    finished_at: str = ""
    new_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    deleted_count: int = 0
    illust_new: int = 0
    illust_updated: int = 0
    illust_skipped: int = 0
    illust_failed: int = 0
    illust_deleted: int = 0
    aborted_reason: str = ""
    auth_expired: bool = False       # 登录失效（Cookie 过期）导致中止
    truncated: bool = False          # 已达「单轮下载上限」，本轮提前收尾，仍有剩余待续抓
    per_author: dict = field(default_factory=dict)


class ScraperEngine:
    def __init__(self, settings: Settings, db: Database, cookie: str, logger, progress_cb=None):
        self.settings = settings
        self.db = db
        self.cookie = cookie
        self.logger = logger
        self.progress_cb = progress_cb or (lambda event: None)
        self._stop_event = threading.Event()
        self.client = PixivClient(settings, cookie, logger, stop_event=self._stop_event)
        self.cookie = cookie or ""
        self.report = RunReport()
        # 分批抓取：本轮已成功下载的作品数（小说 + 图片合计）
        self._batch_downloaded = 0
        self._batch_limit = self._cfg_int("batch_limit", 0)
        # 试运行（dry-run）：只走筛选与更新判定，不下载、不写库
        self.dry_run = False
        self.dry_run_would_download = 0
        # 各子系统的可选项（缺省即历史行为）
        self.popts = paths.output_opts(settings)
        self.eopts = epub_exporter.epub_opts(settings)
        self.mopts = md_index.md_opts(settings)

    # ---- 配置读取小工具（缺字段时回退到历史行为）----
    def _cfg(self, key, default):
        try:
            v = getattr(self.settings, key, default)
        except Exception:
            v = default
        return default if v is None else v

    def _cfg_int(self, key, default):
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return int(default)

    def _split_keywords(self, value: str) -> list:
        return [k.strip() for k in (value or "").replace("，", ",").split(",") if k.strip()]

    def _filter_reason(self, meta) -> str:
        """返回跳过原因；空字符串表示通过筛选。"""
        whitelist = {t.strip() for t in (self._cfg("tag_whitelist", []) or []) if t.strip()}
        if whitelist and not whitelist.intersection(getattr(meta, "tags", []) or []):
            return "未命中标签白名单"
        hay = f"{getattr(meta, 'title', '')}\n{getattr(meta, 'description', '')}"
        kw_in = self._split_keywords(self._cfg("keyword_include", ""))
        if kw_in and not any(k in hay for k in kw_in):
            return "未命中包含关键词"
        kw_ex = self._split_keywords(self._cfg("keyword_exclude", ""))
        if kw_ex and any(k in hay for k in kw_ex):
            return "命中排除关键词"
        min_len = self._cfg_int("min_text_length", 0)
        if min_len > 0:
            size = int(getattr(meta, "text_count", 0) or 0)
            if size > 0 and size < min_len:
                return f"正文不足 {min_len} 字"
        return ""

    def request_stop(self):
        self._stop_event.set()
        self.logger.info("收到手动中止请求，将在当前作品处理完后停止")

    def _check_stop(self):
        if self._stop_event.is_set():
            self.report.aborted_reason = "已被用户手动中止"
            self.logger.info(self.report.aborted_reason)
            raise _AbortRun()

    def _emit(self, kind: str, **payload):
        event = {"kind": kind, **payload}
        try:
            self.progress_cb(event)
        except Exception:
            pass

    def _check_disk(self) -> bool:
        threshold = self.settings.disk_threshold_mb
        roots = {self.settings.epub_root, paths.illust_root(self.settings)}
        for root in roots:
            if not root:
                continue
            drive = os.path.splitdrive(os.path.abspath(root))[0] + os.sep
            free_mb = shutil.disk_usage(drive).free / (1024 * 1024)
            if free_mb < threshold:
                return False
        return True

    def run(self) -> RunReport:
        self.report.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.logger.info("任务开始")
        try:
            if not self._check_disk():
                self.report.aborted_reason = f"磁盘剩余空间不足 {self.settings.disk_threshold_mb} MB，任务暂停"
                self.logger.warning(self.report.aborted_reason)
                return self.report
            try:
                self._auto_backup()
                if self._cfg("author_rename_migrate", True):
                    self._migrate_filenames()
                    self._migrate_illust_paths()
                if self._cfg("retry_failed_each_run", True):
                    self._retry_failed_queue()
                    self._retry_failed_illusts()
                else:
                    self.logger.info("按设置跳过失败队列重试")
                self._recheck_disabled_authors()
                novel_authors = self.db.list_authors(enabled_only=True, kind="novel")
                illust_authors = self.db.list_authors(enabled_only=True, kind="illust")
                total_authors = len(novel_authors) + len(illust_authors)
                done_authors = 0
                self._emit("run_scope", total=total_authors)
                for author in novel_authors:
                    self._guard_failures()
                    self._process_author(author)
                    done_authors += 1
                    self._emit("run_progress", done=done_authors, total=total_authors)
                for author in illust_authors:
                    self._guard_failures()
                    self._process_illust_author(author)
                    done_authors += 1
                    self._emit("run_progress", done=done_authors, total=total_authors)
            except AuthExpiredError:
                self.report.aborted_reason = "登录已失效，请重新导入 Cookie"
                self.report.auth_expired = True
                self.logger.error(self.report.aborted_reason)
            except _BatchLimitReached:
                self.report.truncated = True
                # 异常冒出时正处理的那位作者还没走到尾部汇总，这里把未合并的补上，
                # 保证「触发上限那部作品」的计数不丢、author_done 事件也照发。
                self._merge_unfinished_author_stats()
                self.logger.info(
                    "已达本轮下载上限（%d 部），提前收尾，剩余作品待续抓", self._batch_limit)
            except _AbortRun:
                pass
        finally:
            self.client.close()
            self.report.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(
                "任务结束：小说 新增 %d / 更新 %d / 跳过 %d / 失败 %d；图片 新增 %d / 更新 %d / 失败 %d",
                self.report.new_count, self.report.updated_count,
                self.report.skipped_count, self.report.failed_count,
                self.report.illust_new, self.report.illust_updated,
                self.report.illust_failed,
            )
        return self.report

    def _auto_backup(self):
        """抓取前的滚动自动备份（含 Cookie）。失败只告警，不阻断抓取。"""
        if not self._cfg("auto_backup_enabled", True):
            return
        try:
            from .backup import create_auto_backup
            create_auto_backup(self.settings, self.db, self.cookie,
                               keep=self._cfg_int("auto_backup_keep", 5), logger=self.logger)
        except Exception as e:
            self.logger.warning("自动备份失败（不影响抓取）：%s", e)

    def _recheck_disabled_authors(self):
        """按配置自动复查此前被停用的作者（0 天 = 不复查，历史行为）。"""
        days = self._cfg_int("disabled_recheck_days", 0)
        if days <= 0:
            return
        for kind in ("novel", "illust"):
            for row in self.db.authors_due_for_recheck(days, kind=kind):
                aid = row["author_id"]
                self.logger.info("停用作者 %s 已超过 %d 天，本轮自动复查", aid, days)
                self.db.reenable_author(aid, kind)

    def _guard_failures(self):
        self._check_stop()
        limit = max(1, int(self.settings.max_consecutive_failures))
        if self.client.consecutive_failures >= limit:
            self.report.aborted_reason = f"连续失败 {limit} 次，任务自动暂停，请检查网络与代理"
            self.logger.error(self.report.aborted_reason)
            raise _AbortRun()

    def _register_download(self):
        """每成功下载一部作品调用一次；达到单轮上限时抛出 _BatchLimitReached。

        分批抓取的核心钩子：上限只在「本轮」内生效，抛出后由 run() 捕获，
        标记 truncated 并提前收尾，剩余作品留给下一轮（定时器按间隔自动续抓）。
        """
        if self._batch_limit <= 0:
            return
        self._batch_downloaded += 1
        if self._batch_downloaded >= self._batch_limit:
            raise _BatchLimitReached()

    def _handle_author_missing(self, aid: str, author: dict, kind: str = "novel") -> None:
        """作者账号已注销 / 不存在时，按 author_404_action 处理。"""
        action = str(self._cfg("author_404_action", "disable"))
        if action == "disable":
            self.db.set_author_enabled(aid, False, kind=kind,
                                       reason=DISABLE_REASON_DEACTIVATED)
            self.logger.warning(
                "作者 %s（%s）账号已注销或不存在，已自动停用，本地作品保留",
                aid, author.get("author_name") or "",
            )
            if kind == "illust" and not self.report.aborted_reason:
                self.report.aborted_reason = f"作者 {aid} 账号已注销或不存在，插画名单已自动停用"
        elif action == "skip":
            self.logger.warning("作者 %s 账号已注销或不存在，按设置本轮跳过且保持原状态", aid)
        else:
            self.logger.warning("作者 %s 账号已注销或不存在，按设置保持启用（仅记录）", aid)

    def _ensure_author_alive(self, aid: str, author: dict, kind: str = "novel") -> bool:
        """确认作者账号仍存在；False 表示已注销且已按配置处理，调用方应跳过该作者。

        已注销账号的作品列表接口仍返回 200（只是列表为空），因此必须单独
        查一次账号主体接口才能判定，否则会把已注销账号当成「零作品账号」放过。
        """
        try:
            exists, name = api.get_user_status(self.client, aid)
        except AuthExpiredError:
            raise
        except Exception as e:
            # 查不到结论时本轮仍按正常处理（不误伤），但必须说清楚这类作者
            # 有可能已注销而没被发现；要确认就到作者管理页点「检查账号状态」。
            self.logger.warning(
                "作者 %s 账号状态检查失败（网络 / 风控 / 解析异常），本轮按正常处理：%s。"
                "若该账号其实已注销，这里会漏判，可在作者管理页点「检查账号状态」复核",
                aid, e)
            return True
        if not exists:
            self._handle_author_missing(aid, author, kind)
            return False
        if name and name != (author.get("author_name") or ""):
            # 必须带 kind：同一作者可同时在小说与插画名单，不带会连另一名单一起改
            self.db.update_author_name(aid, name, kind=kind)
            author["author_name"] = name
        return True

    def _retry_failed_queue(self):
        # 重试次数耗尽的作品不再自动重试：永久性失败（作者彻底删除、版权限制等）
        # 每轮都重试一次只是白白消耗请求配额，留给用户在界面手动处理。
        max_retry = getattr(self.db, "MAX_AUTO_RETRIES", 5)
        skipped, queue = [], []
        for n in self.db.failed_novels():
            if n["status"] == Status.FAILED_AUTH:
                continue
            if int(n.get("retry_count") or 0) >= max_retry:
                skipped.append(n)
            else:
                queue.append(n)
        if skipped:
            self.logger.info("失败队列中 %d 部已重试 %d 次仍失败，本轮跳过（可在失败列表手动重试）",
                             len(skipped), max_retry)
        if not queue:
            return
        self.logger.info("优先重试失败队列：%d 部", len(queue))
        # 重试的结果必须汇总进 report，否则界面显示「失败 0」而实际仍有失败
        stat = {"new": 0, "updated": 0, "skipped": 0, "failed": 0}
        for novel in queue:
            self._guard_failures()
            try:
                self._process_novel(novel["novel_id"], novel["author_id"],
                                    is_retry=True, stat=stat)
            except (_AbortRun, _BatchLimitReached):
                raise  # 中止/达到上限必须往外传，交由 run() 统一收尾
            except Exception:
                # 单部作品的未预期异常不能中断整个重试队列，
                # 否则后面的作品连重试的机会都没有。
                self.logger.warning("重试 %s 时发生未预期错误", novel["novel_id"],
                                    exc_info=True)
                stat["failed"] += 1
                self.db.set_novel_status(novel["novel_id"], Status.FAILED_PARSE,
                                         "重试时发生未预期错误")
        self.report.new_count += stat["new"]
        self.report.updated_count += stat["updated"]
        self.report.skipped_count += stat["skipped"]
        self.report.failed_count += stat["failed"]

    def _process_author(self, author: dict):
        aid = author["author_id"]
        stat = {"new": 0, "updated": 0, "skipped": 0, "failed": 0, "deleted": 0,
                "series_deleted": 0,
                "illust_new": 0, "illust_updated": 0, "illust_skipped": 0,
                "illust_failed": 0, "illust_deleted": 0}
        self.report.per_author[aid] = stat
        self._emit("author_start", author_id=aid, author_name=author.get("author_name") or aid)
        try:
            if author.get("pending_validate") or not author.get("author_name"):
                if not self._ensure_author_alive(aid, author, "novel"):
                    self._finish_author_stat(stat, aid)
                    return
            remote_ids, _ = api.get_user_profile_ids(self.client, aid)
            remote_ids = set(remote_ids)
            if not remote_ids:
                # 远端零作品：需区分「已注销」与「还没发过作品的新账号」
                if not self._ensure_author_alive(aid, author, "novel"):
                    self._finish_author_stat(stat, aid)
                    return
        except AuthExpiredError:
            raise
        except PixivRequestError as e:
            if e.kind == "not_found":
                self._handle_author_missing(aid, author, "novel")
            else:
                stat["failed"] += 1
                self.logger.error("作者 %s 作品列表获取失败：%s", aid, e)
            self._finish_author_stat(stat, aid)
            return
        except Exception as e:
            stat["failed"] += 1
            self.logger.error("作者 %s 作品列表获取失败：%s", aid, e)
            self._finish_author_stat(stat, aid)
            return

        local_map = {n["novel_id"]: n for n in self.db.novels_by_author(aid)}
        if self._cfg("remote_deleted_action", "keep") == "keep":
            for nid, rec in local_map.items():
                if nid not in remote_ids and rec["status"] in (Status.OK, Status.OUTDATED):
                    self.db.set_novel_status(nid, Status.DELETED)
                    stat["deleted"] += 1
                    self.logger.info("作品 %s 远端已删除，本地保留", nid)
                elif nid in remote_ids and rec["status"] == Status.DELETED:
                    # 之前判过「远端已删除」，现在又出现在列表里：多半是那次接口
                    # 返回不全（风控 / 临时故障）误判的。恢复成正常，让它重新参与
                    # 更新判定；本地文件缺失的会由 redownload_missing 自动补下。
                    self.db.set_novel_status(nid, Status.OK)
                    self.logger.info("作品 %s 远端重新上线，恢复为正常", nid)

        remote_name = ""
        missing_files = []
        if self._cfg("redownload_missing", True):
            missing_files = [
                nid for nid, rec in local_map.items()
                if rec["status"] == Status.OK and not (rec.get("local_path") and os.path.exists(rec["local_path"]))
            ]
        if missing_files:
            self.logger.info("作者 %s 有 %d 部作品本地文件缺失，重新下载", aid, len(missing_files))
        new_ids = sorted({nid for nid in remote_ids if nid not in local_map}, key=int, reverse=True)
        cap = self._cfg_int("max_novels_per_author", 0)
        if cap > 0 and len(new_ids) > cap:
            self.logger.info("作者 %s 远端新增 %d 部，按设置本次只处理最新的 %d 部", aid, len(new_ids), cap)
            new_ids = new_ids[:cap]
        queue = sorted(set(missing_files) | set(new_ids), key=int)
        for nid in queue:
            self._guard_failures()
            name = self._process_novel(nid, aid, stat=stat)
            if name and not remote_name:
                remote_name = name

        series_ids = self.db.series_ids_by_author(aid)
        for series_id in series_ids:
            self._guard_failures()
            if not self.db.should_poll_series(
                    series_id, self._cfg_int("series_recheck_days", SERIES_RECHECK_DAYS)):
                continue
            try:
                series = api.get_series_novel_ids(self.client, series_id)
            except PixivRequestError as e:
                if e.kind == "not_found":
                    self.db.mark_series_deleted(series_id, aid)
                    stat["series_deleted"] += 1
                    self.logger.info("系列 %s 已不存在（404），标记为已删除，%d 天后重试", series_id, SERIES_RECHECK_DAYS)
                else:
                    self.logger.warning("系列 %s 轮询失败：%s", series_id, e)
                continue
            except Exception as e:
                self.logger.warning("系列 %s 轮询失败：%s", series_id, e)
                continue
            self.db.unmark_series_deleted(series_id)
            for nid in series.novel_ids:
                rec = self.db.get_novel(nid)
                if rec is None or rec["status"] == Status.OUTDATED:
                    name = self._process_novel(nid, aid, stat=stat)
                    if name and not remote_name:
                        remote_name = name

        # 「系列全部 404」是个强信号：这类作者单篇列表往往还有返回（于是不会触发
        # 上面的零作品检查），但系列分组已经全没了，账号多半已注销。这里补查一次
        # 账号主体接口，把这条漏判路径堵上；没有系列的作者不受影响。
        if series_ids and self.db.deleted_series_ids(aid).issuperset(series_ids):
            self._guard_failures()
            if not self._ensure_author_alive(aid, author, "novel"):
                self._finish_author_stat(stat)
                return

        if remote_name and remote_name != (author.get("author_name") or ""):
            self.logger.info("作者 %s 昵称变更：%s -> %s", aid, author.get("author_name"), remote_name)
            self.db.update_author_name(aid, remote_name)
            if self._cfg("author_rename_migrate", True):
                self.logger.info("迁移文件至新目录")
                # 只有这位作者改了名，只迁移他的文件即可
                self._migrate_filenames(aid)
                self._migrate_illust_paths(aid)
            else:
                self.logger.info("按设置跳过目录迁移，已下载文件保留在原目录")

        self.db.touch_author_fetch(aid, kind="novel")
        self._refresh_author_md(aid, stat)
        self._merge_stat(stat, aid)
        self._emit_author_done(aid, stat)

    def _migrate_filenames(self, author_id: str = None):
        """按当前命名规则重命名 EPUB。

        author_id 为空时处理全部作者（抓取开始前的例行对齐）；
        指定时只处理该作者 —— 抓取途中某位作者改昵称时就是这种情况，
        没必要为此把整张作品表再扫一遍。
        """
        if author_id:
            recs = [r for r in self.db.novels_by_author(author_id)
                    if r.get("status") == Status.OK]
        else:
            recs = self.db.ok_novels()
        for rec in recs:
            old = rec.get("local_path") or ""
            if not old or not os.path.exists(old):
                continue
            desired = paths.novel_path(self.settings.epub_root, rec, self.settings.name_pattern, self.popts)
            desired = paths.resolve_collision(desired, rec, old)
            if os.path.abspath(desired) == os.path.abspath(old):
                continue
            try:
                os.makedirs(os.path.dirname(desired), exist_ok=True)
                os.rename(old, desired)
                self.db.upsert_novel({"novel_id": rec["novel_id"], "local_path": desired})
                self.logger.info("按命名规则重命名：%s -> %s", os.path.basename(old), os.path.basename(desired))
                try:
                    os.removedirs(os.path.dirname(old))
                except OSError:
                    pass
            except OSError:
                self.logger.warning("文件重命名失败：%s", old, exc_info=True)

    # ---------------- 插画/漫画 ----------------
    def _process_illust_author(self, author: dict):
        aid = author["author_id"]
        stat = {"new": 0, "updated": 0, "skipped": 0, "failed": 0, "deleted": 0,
                "illust_new": 0, "illust_updated": 0, "illust_skipped": 0,
                "illust_failed": 0, "illust_deleted": 0}
        self.report.per_author[aid] = stat
        self._emit("author_start", author_id=aid, author_name=author.get("author_name") or aid)
        try:
            if author.get("pending_validate") or not author.get("author_name"):
                if not self._ensure_author_alive(aid, author, "illust"):
                    self._report_illust_stat(stat)
                    return
            _, remote_illust_ids = api.get_user_profile_ids(self.client, aid)
            remote_illust_ids = set(remote_illust_ids)
            if not remote_illust_ids:
                # 远端零作品：需区分「已注销」与「还没发过作品的新账号」
                if not self._ensure_author_alive(aid, author, "illust"):
                    self._report_illust_stat(stat)
                    return
        except AuthExpiredError:
            raise
        except PixivRequestError as e:
            if e.kind == "not_found":
                self._handle_author_missing(aid, author, "illust")
            else:
                stat["illust_failed"] += 1
                self.logger.error("作者 %s 插画列表获取失败：%s", aid, e)
            self._report_illust_stat(stat)
            return
        except Exception as e:
            stat["illust_failed"] += 1
            self.logger.error("作者 %s 插画列表获取失败：%s", aid, e)
            self._report_illust_stat(stat)
            return

        self._process_author_illusts(author, remote_illust_ids, stat)

        recs = self.db.illusts_by_author(aid)
        remote_name = next((r["author_name"] for r in recs if r.get("author_name")), "")
        if remote_name and remote_name != (author.get("author_name") or ""):
            self.logger.info("作者 %s 昵称变更：%s -> %s", aid, author.get("author_name"), remote_name)
            self.db.update_author_name(aid, remote_name, kind="illust")
            if self._cfg("author_rename_migrate", True):
                self.logger.info("迁移图片至新目录")
                # 只有这位作者改了名，只迁移他的文件即可
                self._migrate_illust_paths(aid)
                self._migrate_filenames(aid)
            else:
                self.logger.info("按设置跳过目录迁移，已下载图片保留在原目录")

        self.db.touch_author_fetch(aid, kind="illust")
        self._refresh_author_md(aid, stat)
        self._report_illust_stat(stat)
        self._emit_author_done(aid, stat)

    def _merge_stat(self, stat: dict, author_id: str = "") -> None:
        """把单作者统计并入总报告（幂等，重复调用不会重复累计）。

        正常完成、早退、分批截断三条路径都汇到这里；stat 带 _merged 标记，
        异常打断后由 _merge_unfinished_author_stats 兜底补一次也不重复。
        """
        if stat.get("_merged"):
            return
        stat["_merged"] = True
        self.report.new_count += stat.get("new", 0)
        self.report.updated_count += stat.get("updated", 0)
        self.report.skipped_count += stat.get("skipped", 0)
        self.report.failed_count += stat.get("failed", 0)
        self.report.deleted_count += stat.get("deleted", 0)
        self.report.illust_new += stat.get("illust_new", 0)
        self.report.illust_updated += stat.get("illust_updated", 0)
        self.report.illust_skipped += stat.get("illust_skipped", 0)
        self.report.illust_failed += stat.get("illust_failed", 0)
        self.report.illust_deleted += stat.get("illust_deleted", 0)

    def _merge_unfinished_author_stats(self):
        """分批截断 / 异常打断时，把 per_author 里未合并的统计补进 report。"""
        for aid, stat in self.report.per_author.items():
            if not stat.get("_merged"):
                self._merge_stat(stat, aid)

    def _finish_author_stat(self, stat: dict, author_id: str = ""):
        """把单作者统计并入总报告，并发 author_done 事件。

        中途 return 的分支（账号已注销、列表获取失败等）也必须走这里：
        只累加 failed 会丢掉其余计数，并且不发 author_done，
        界面日志里该作者就像凭空消失了一样。
        """
        if not author_id:
            author_id = stat.get("author_id", "")
        self._merge_stat(stat, author_id)
        self._emit_author_done(author_id, stat)

    def _emit_author_done(self, author_id: str, stat: dict) -> None:
        """发 author_done 事件。

        统一走这里是为了避免一个反复踩的坑：stat 里可能也带 author_id 键，
        写成 _emit("author_done", author_id=aid, **stat) 会因关键字重复抛
        TypeError，而调用处的 except 会把它吞掉 —— 事件就静默地发不出来了。
        """
        payload = {k: v for k, v in stat.items() if not k.startswith("_")}
        payload["author_id"] = author_id
        try:
            self._emit("author_done", **payload)
        except Exception:
            pass

    def _report_illust_stat(self, stat: dict):
        self._merge_stat(stat)

    def _illust_ext(self, rec: dict) -> str:
        old = rec.get("local_path") or ""
        ext = os.path.splitext(old)[1]
        return ext if ext else ".jpg"

    def _migrate_illust_paths(self, author_id: str = None):
        """按当前命名规则迁移图片；author_id 存在时只处理该作者。"""
        if author_id:
            recs = [r for r in self.db.illusts_by_author(author_id)
                    if r.get("status") == Status.OK]
        else:
            recs = self.db.ok_illusts()
        root = paths.illust_root(self.settings)
        for rec in recs:
            old = rec.get("local_path") or ""
            if not old or not os.path.exists(old):
                continue
            desired = paths.illust_path(root, rec, self._illust_ext(rec), self.popts)
            if os.path.abspath(desired) == os.path.abspath(old):
                continue
            try:
                os.makedirs(os.path.dirname(desired), exist_ok=True)
                os.rename(old, desired)
                self.db.upsert_illust({"illust_id": rec["illust_id"], "local_path": desired})
                self.logger.info("图片迁移：%s -> %s", os.path.basename(old), os.path.basename(desired))
                try:
                    os.removedirs(os.path.dirname(old))
                except OSError:
                    pass
            except OSError:
                self.logger.warning("图片迁移失败：%s", old, exc_info=True)

    def _retry_failed_illusts(self):
        queue = [i for i in self.db.failed_illusts() if i["status"] != Status.FAILED_AUTH]
        if not queue:
            return
        self.logger.info("优先重试失败图片：%d 幅", len(queue))
        stat = {"illust_new": 0, "illust_updated": 0, "illust_skipped": 0,
                "illust_failed": 0, "illust_deleted": 0}
        for item in queue:
            self._guard_failures()
            self._process_illust(item["illust_id"], item["author_id"], stat)
        self.report.illust_new += stat["illust_new"]
        self.report.illust_updated += stat["illust_updated"]
        self.report.illust_skipped += stat["illust_skipped"]
        self.report.illust_failed += stat["illust_failed"]
        self.report.illust_deleted += stat["illust_deleted"]

    def _process_author_illusts(self, author: dict, remote_ids: set, stat: dict):
        aid = author["author_id"]
        local_map = {i["illust_id"]: i for i in self.db.illusts_by_author(aid)}
        if self._cfg("remote_deleted_action", "keep") == "keep":
            for iid, rec in local_map.items():
                if iid not in remote_ids and rec["status"] in (Status.OK, Status.OUTDATED):
                    self.db.set_illust_status(iid, Status.DELETED)
                    stat["illust_deleted"] += 1
                    self.logger.info("图片 %s 远端已删除，本地保留", iid)

        missing = []
        if self._cfg("redownload_missing", True):
            missing = [
                iid for iid, rec in local_map.items()
                if rec["status"] == Status.OK and not (rec.get("local_path") and os.path.exists(rec["local_path"]))
            ]
        if missing:
            self.logger.info("作者 %s 有 %d 幅图片本地文件缺失，重新下载", aid, len(missing))
        new_ids = sorted({iid for iid in remote_ids if iid not in local_map}, key=int, reverse=True)
        cap = self._cfg_int("max_novels_per_author", 0)
        if cap > 0 and len(new_ids) > cap:
            self.logger.info("作者 %s 插画远端新增 %d 幅，按设置本次只处理最新的 %d 幅", aid, len(new_ids), cap)
            new_ids = new_ids[:cap]
        queue = sorted(set(missing) | set(new_ids), key=int)
        for iid in queue:
            self._guard_failures()
            self._process_illust(iid, aid, stat)

    def _process_illust(self, illust_id: str, author_id: str, stat: dict):
        try:
            meta = api.get_illust_detail(self.client, illust_id)
        except PixivRequestError as e:
            if e.kind == "not_found":
                if self._cfg("remote_deleted_action", "keep") == "keep":
                    self.db.set_illust_status(illust_id, Status.DELETED)
                    self.logger.info("图片 %s 远端已删除，本地保留", illust_id)
                else:
                    self.logger.info("图片 %s 远端已删除，按设置不标记状态", illust_id)
                return
            self._mark_illust_failed(illust_id, author_id, e)
            stat["illust_failed"] += 1
            return
        except RiskControlError as e:
            self._mark_illust_failed(illust_id, author_id, e, Status.FAILED_403)
            stat["illust_failed"] += 1
            return

        existing = self.db.get_illust(illust_id)
        if not self.settings.r18_enabled and meta.x_restrict > 0:
            self.logger.info("图片 %s 为 R-18，按设置跳过", illust_id)
            stat["illust_skipped"] += 1
            return
        blacklist = {t.strip() for t in self.settings.tag_blacklist if t.strip()}
        if blacklist and blacklist.intersection(meta.tags):
            self.logger.info("图片 %s 命中标签黑名单，跳过下载", illust_id)
            self._index_illust(meta, existing, Status.SKIPPED)
            stat["illust_skipped"] += 1
            return
        reason = self._filter_reason(meta)
        if reason:
            self.logger.info("图片 %s %s，跳过下载", illust_id, reason)
            self._index_illust(meta, existing, Status.SKIPPED)
            stat["illust_skipped"] += 1
            return

        update_mode = str(self._cfg("update_mode", "timestamp"))
        if existing and existing["status"] == Status.OK:
            local_file = existing.get("local_path") or ""
            file_exists = bool(local_file) and os.path.exists(local_file)
            if update_mode == "never" and file_exists:
                stat["illust_skipped"] += 1
                return
            if update_mode == "timestamp":
                remote_ts = meta.upload_date
                local_ts = existing.get("remote_updated_at") or ""
                if remote_ts and local_ts and remote_ts <= local_ts and file_exists:
                    stat["illust_skipped"] += 1
                    return

        is_update = existing is not None
        if self.dry_run:
            self.dry_run_would_download += 1
            stat["illust_updated" if is_update else "illust_new"] += 1
            self.logger.info("[试运行] 图片 %s《%s》本应%s", illust_id, meta.title,
                             "更新" if is_update else "下载")
            return
        try:
            urls = api.get_illust_page_urls(self.client, illust_id)
            if len(urls) < meta.page_count:
                meta.page_count = len(urls)
            rec = self._index_illust(meta, existing, Status.OK)
            root = paths.illust_root(self.settings)
            blobs = []
            for idx, url in enumerate(urls):
                blob = api.download_illust_image(self.client, url, illust_id)
                ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
                blobs.append((idx, ext, blob))
            first_ext = blobs[0][1] if blobs else ".jpg"
            out_path = paths.illust_path(root, rec, first_ext, self.popts)
            old_path = existing.get("local_path") if existing else ""
            multipage = int(meta.page_count or 1) > 1
            if multipage and self.popts["illust_multipage_style"] == "folder":
                os.makedirs(out_path, exist_ok=True)
                prefix = f"{illust_id}_p"
                for stale in os.listdir(out_path):
                    if stale.startswith(prefix):
                        try:
                            os.remove(os.path.join(out_path, stale))
                        except OSError:
                            pass
                for idx, ext, blob in blobs:
                    fname = f"{illust_id}_p{idx}{ext}"
                    utils.atomic_write_bytes(os.path.join(out_path, fname), blob)
            elif multipage:
                # 平铺模式：多页直接放在作者目录下
                parent = os.path.dirname(out_path)
                os.makedirs(parent, exist_ok=True)
                stem, _cur_ext = os.path.splitext(out_path)
                prefix = f"{os.path.basename(stem)}_p"
                for stale in os.listdir(parent):
                    if stale.startswith(prefix):
                        try:
                            os.remove(os.path.join(parent, stale))
                        except OSError:
                            pass
                first_file = ""
                for idx, ext, blob in blobs:
                    fpath = os.path.join(parent, f"{prefix}{idx}{ext}")
                    utils.atomic_write_bytes(fpath, blob)
                    if idx == 0:
                        first_file = fpath
                out_path = first_file or out_path
            else:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                utils.atomic_write_bytes(out_path, blobs[0][2])
            if self._cfg("replace_old_file", True):
                if old_path and os.path.abspath(old_path) != os.path.abspath(out_path) and os.path.exists(old_path):
                    if os.path.isdir(old_path):
                        shutil.rmtree(old_path, ignore_errors=True)
                    else:
                        os.remove(old_path)
            self.db.upsert_illust({
                "illust_id": illust_id,
                "local_path": out_path,
                "status": Status.OK,
                "error_reason": "",
                "last_fetch_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            stat["illust_updated" if is_update else "illust_new"] += 1
            self._emit("illust_done", illust_id=illust_id, title=meta.title, updated=is_update)
            self.logger.info("图片 %s《%s》（%d 页）%s", illust_id, meta.title, meta.page_count,
                             "更新完成" if is_update else "下载完成")
            self._register_download()
        except AuthExpiredError:
            raise
        except _BatchLimitReached:
            raise
        except Exception as e:
            self.logger.error("图片 %s 处理失败：%s\n%s", illust_id, e, traceback.format_exc())
            self._mark_illust_failed(illust_id, author_id, e)
            stat["illust_failed"] += 1

    def _index_illust(self, meta, existing, status: str) -> dict:
        rec = {
            "illust_id": meta.illust_id,
            "author_id": meta.author_id,
            "author_name": meta.author_name,
            "title": meta.title,
            "page_count": meta.page_count,
            "tags": ",".join(meta.tags),
            "x_restrict": meta.x_restrict,
            "status": status,
            "remote_updated_at": meta.upload_date,
            "publish_date": meta.create_date,
        }
        if existing and existing.get("local_path"):
            rec["local_path"] = existing["local_path"]
        self.db.upsert_illust(rec)
        merged = dict(existing or {})
        merged.update(rec)
        return merged

    def _mark_illust_failed(self, illust_id: str, author_id: str, err: Exception, status: str = None):
        if status is None:
            status = Status.FAILED_AUTH if isinstance(err, AuthExpiredError) else (
                Status.FAILED_403 if isinstance(err, RiskControlError) else (
                    Status.FAILED_PARSE if isinstance(err, PixivRequestError) and err.kind == "parse"
                    else Status.FAILED_NETWORK
                )
            )
        existing = self.db.get_illust(illust_id)
        if existing:
            self.db.set_illust_status(illust_id, status, str(err)[:500])
        else:
            self.db.upsert_illust({
                "illust_id": illust_id, "author_id": author_id,
                "status": status, "error_reason": str(err)[:500],
            })

    def _process_novel(self, novel_id: str, author_id: str, is_retry: bool = False, stat: dict = None):
        stat = stat if stat is not None else {"new": 0, "updated": 0, "skipped": 0, "failed": 0}
        try:
            meta, content = api.get_novel_detail(self.client, novel_id)
        except PixivRequestError as e:
            if e.kind == "not_found":
                if self._cfg("remote_deleted_action", "keep") == "keep":
                    self.db.set_novel_status(novel_id, Status.DELETED)
                else:
                    self.logger.info("作品 %s 远端已删除，按设置不标记状态", novel_id)
                return ""
            self._mark_failed(novel_id, author_id, e)
            stat["failed"] += 1
            return ""
        except RiskControlError as e:
            self._mark_failed(novel_id, author_id, e, Status.FAILED_403)
            stat["failed"] += 1
            return ""

        existing = self.db.get_novel(novel_id)
        if not self.settings.r18_enabled and meta.x_restrict > 0:
            self.logger.info("作品 %s 为 R-18，按设置跳过", novel_id)
            stat["skipped"] += 1
            return meta.author_name
        blacklist = {t.strip() for t in self.settings.tag_blacklist if t.strip()}
        if blacklist and blacklist.intersection(meta.tags):
            self.logger.info("作品 %s 命中标签黑名单，跳过下载", novel_id)
            self._index_novel(meta, existing, Status.SKIPPED)
            stat["skipped"] += 1
            return meta.author_name
        reason = self._filter_reason(meta)
        if reason:
            self.logger.info("作品 %s %s，跳过下载", novel_id, reason)
            self._index_novel(meta, existing, Status.SKIPPED)
            stat["skipped"] += 1
            return meta.author_name

        update_mode = str(self._cfg("update_mode", "timestamp"))
        if existing and existing["status"] == Status.OK:
            local_file = existing.get("local_path") or ""
            file_exists = bool(local_file) and os.path.exists(local_file)
            if update_mode == "never" and file_exists:
                stat["skipped"] += 1
                return meta.author_name
            if update_mode == "timestamp":
                remote_ts = meta.upload_date
                local_ts = existing.get("remote_updated_at") or ""
                if remote_ts and local_ts and remote_ts <= local_ts and file_exists:
                    stat["skipped"] += 1
                    return meta.author_name

        is_update = existing is not None
        if self.dry_run:
            # 试运行：已通过所有筛选，本应下载。只计数，不做任何网络写入。
            self.dry_run_would_download += 1
            stat["updated" if is_update else "new"] += 1
            self.logger.info("[试运行] %s《%s》本应%s", novel_id, meta.title,
                             "更新" if is_update else "新下载")
            return meta.author_name
        try:
            title_style = str(self._cfg("chapter_title_style", "marker"))
            converted = convert_novel_markup(content, title_style=title_style)
            if converted.unknown_markers:
                self.logger.warning("作品 %s 含未识别标记：%s", novel_id, ", ".join(converted.unknown_markers[:5]))
            illust_files = {}
            if self._cfg("epub_download_inserts", True):
                illust_files = self._fetch_illusts(novel_id, converted.illust_ids, meta.embedded_images)
                if illust_files:
                    markup_map = {iid: fn for iid, (fn, _blob) in illust_files.items()}
                    converted = convert_novel_markup(content, markup_map, title_style=title_style)
            else:
                self.logger.info("作品 %s 按设置跳过内嵌插图下载", novel_id)
            images = {fn: blob for fn, blob in illust_files.values()} if illust_files else {}
            cover = None
            cover_name = "cover.jpg"
            if meta.cover_url:
                try:
                    cover = api.download_image(self.client, meta.cover_url)
                    cover_name = os.path.basename(meta.cover_url.split("?")[0]) or "cover.jpg"
                except Exception:
                    self.logger.warning("作品 %s 封面下载失败", meta.cover_url, exc_info=True)
            if cover is None and images and self._cfg("epub_cover_fallback", True):
                cover_name, cover = next(iter(images.items()))
                self.logger.info("作品 %s 无封面，使用第一张插图作为封面", novel_id)

            rec = self._index_novel(meta, existing, Status.OK)
            out_path = paths.novel_path(self.settings.epub_root, rec, self.settings.name_pattern, self.popts)
            old_path = existing.get("local_path") if existing else ""
            out_path = paths.resolve_collision(out_path, rec, old_path)
            build_epub(meta, converted.chapters, images, out_path, cover, cover_name, self.eopts)
            if self._cfg("replace_old_file", True):
                if old_path and os.path.abspath(old_path) != os.path.abspath(out_path) and os.path.exists(old_path):
                    os.remove(old_path)
            self.db.upsert_novel({
                "novel_id": novel_id,
                "local_path": out_path,
                "status": Status.OK,
                "error_reason": "",
                "last_fetch_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            stat["updated" if is_update else "new"] += 1
            self._emit("novel_done", novel_id=novel_id, title=meta.title, updated=is_update)
            self.logger.info("作品 %s《%s》%s", novel_id, meta.title, "重打包完成" if is_update else "下载完成")
            self._register_download()
        except AuthExpiredError:
            raise
        except _BatchLimitReached:
            raise
        except Exception as e:
            self.logger.error("作品 %s 处理失败：%s\n%s", novel_id, e, traceback.format_exc())
            self._mark_failed(novel_id, author_id, e)
            stat["failed"] += 1
        return meta.author_name

    def _fetch_illusts(self, novel_id: str, illust_ids: list, embedded_images: dict = None) -> dict:
        result = {}
        if not illust_ids:
            return result
        embedded_images = embedded_images or {}
        site_ids = [iid for iid in illust_ids if iid not in embedded_images]
        info = {}
        if site_ids:
            try:
                info = api.get_insert_illusts(self.client, novel_id, site_ids)
            except Exception:
                self.logger.warning("作品 %s 内嵌插图信息获取失败", novel_id, exc_info=True)
        for iid in illust_ids:
            if iid in embedded_images:
                url = embedded_images[iid]
            else:
                entry = info.get(str(iid)) or {}
                url = (entry.get("illust") or {}).get("url") or entry.get("url") or ""
                if not url:
                    urls = (entry.get("illust") or {}).get("urls") or {}
                    url = urls.get("original") or urls.get("regular") or ""
            if not url:
                continue
            try:
                blob = api.download_image(self.client, url)
                ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
                result[iid] = (f"{iid}{ext}", blob)
            except PixivRequestError as e:
                self.logger.warning("插图 %s 下载失败（%s）", iid, e)
        return result

    def _index_novel(self, meta, existing, status: str) -> dict:
        rec = {
            "novel_id": meta.novel_id,
            "author_id": meta.author_id,
            "author_name": meta.author_name,
            "title": meta.title,
            "series_id": meta.series_id,
            "series_title": meta.series_title,
            "series_order": meta.series_order,
            "description": meta.description,
            "tags": ",".join(meta.tags),
            "x_restrict": meta.x_restrict,
            "status": status,
            "remote_updated_at": meta.upload_date,
            "publish_date": meta.create_date,
        }
        if existing and existing.get("local_path"):
            rec["local_path"] = existing["local_path"]
        self.db.upsert_novel(rec)
        merged = dict(existing or {})
        merged.update(rec)
        return merged

    def _mark_failed(self, novel_id: str, author_id: str, err: Exception, status: str = None):
        if status is None:
            status = Status.FAILED_AUTH if isinstance(err, AuthExpiredError) else (
                Status.FAILED_403 if isinstance(err, RiskControlError) else (
                    Status.FAILED_PARSE if isinstance(err, PixivRequestError) and err.kind == "parse"
                    else Status.FAILED_NETWORK
                )
            )
        existing = self.db.get_novel(novel_id)
        if existing:
            self.db.set_novel_status(novel_id, status, str(err)[:500])
        else:
            self.db.upsert_novel({
                "novel_id": novel_id, "author_id": author_id,
                "status": status, "error_reason": str(err)[:500],
            })
        # 失败就累加重试计数，供「重试次数耗尽则跳过自动重试」使用
        try:
            self.db.bump_novel_retry(novel_id)
        except Exception:
            pass

    def _refresh_author_md(self, author_id: str, run_stat: dict = None):
        # 抓取流程遵守「生成作者索引 MD」开关；「一键重建索引」会直接调用
        # 模块级 refresh_author_md，不受此开关影响（属于用户的显式操作）。
        if not self._cfg("write_md_index", True):
            self.logger.info("按设置跳过作者 %s 的 MD 索引生成", author_id)
            return
        refresh_author_md(self.settings, self.db, self.logger, author_id, run_stat,
                          popts=self.popts, mopts=self.mopts)


class _AbortRun(Exception):
    pass


class _BatchLimitReached(Exception):
    """单轮下载达到上限，提前收尾（不是错误，是用户配置的分批抓取）。"""


def refresh_author_md(settings, db, logger, author_id: str, run_stat: dict = None,
                      popts: dict = None, mopts: dict = None) -> bool:
    """为单个作者写入 / 刷新 Markdown 索引（纯本地，不联网）。

    抓取流程与「一键重建索引」共用同一套逻辑，避免两处实现走偏。
    """
    popts = popts if popts is not None else paths.output_opts(settings)
    mopts = mopts if mopts is not None else md_index.md_opts(settings)

    all_rows = [a for a in db.list_authors() if a["author_id"] == author_id]
    author = next((a for a in all_rows if a.get("kind") == "novel"), None) or (all_rows[0] if all_rows else None)
    if not author:
        return False
    name = author.get("author_name") or ""
    novels = db.novels_by_author(author_id)
    illusts = db.illusts_by_author(author_id)
    in_novel_list = any(a.get("kind") == "novel" for a in all_rows)
    in_illust_list = any(a.get("kind") == "illust" for a in all_rows)

    path = paths.author_md_path(settings, author_id, name, popts)
    illust_path = paths.author_illust_md_path(settings, author_id, name, popts)
    write_novel = in_novel_list or novels or os.path.exists(path)
    write_illust = in_illust_list or illusts or os.path.exists(illust_path)
    deleted_series = db.deleted_series_ids(author_id) if write_novel else set()
    try:
        if write_novel:
            write_author_md(path, author, novels, run_stat, deleted_series, mopts)
        if write_illust:
            write_illust_md(illust_path, author, illusts, run_stat, mopts)
    except Exception:
        logger.warning("作者 %s MD 索引写入失败", author_id, exc_info=True)
        return False

    # 清理旧昵称遗留的重复索引
    cleanup_dirs = {os.path.dirname(path), os.path.dirname(illust_path)}
    if settings.md_separate:
        if settings.md_root:
            cleanup_dirs.add(settings.md_root)
        imr = paths.illust_md_root(settings)
        if imr:
            cleanup_dirs.add(imr)
    if os.path.isdir(settings.epub_root):
        for d in os.listdir(settings.epub_root):
            full_dir = os.path.join(settings.epub_root, d)
            if d.startswith(f"{author_id}_") and os.path.isdir(full_dir):
                cleanup_dirs.add(full_dir)
    keep = {os.path.abspath(path), os.path.abspath(illust_path)}
    for d in cleanup_dirs:
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            full = os.path.join(d, fname)
            if (fname.startswith(f"{author_id}_") and fname.endswith(".md")
                    and os.path.abspath(full) not in keep):
                try:
                    os.remove(full)
                    logger.info("清理旧昵称 MD 文件：%s", fname)
                except OSError:
                    pass
    return True


def rebuild_md_indexes(settings, db, logger, progress_cb=None) -> dict:
    """离线重建全部作者的 Markdown 索引：不联网、不重新下载作品。

    progress_cb(done, total, author_id)
    """
    report = {"authors": 0, "written": 0, "failed": 0}
    seen = []
    known = set()
    for a in db.list_authors():
        aid = a["author_id"]
        if aid in known:
            continue
        known.add(aid)
        seen.append(aid)
    total = len(seen)
    for aid in seen:
        report["authors"] += 1
        try:
            if refresh_author_md(settings, db, logger, aid):
                report["written"] += 1
            else:
                report["failed"] += 1
        except Exception as e:
            report["failed"] += 1
            logger.warning("作者 %s 索引重建失败：%s", aid, e, exc_info=True)
        if progress_cb:
            try:
                # 返回 False 表示调用方请求中止（例如用户点了取消），立即收尾
                if progress_cb(report["authors"], total, aid) is False:
                    break
            except Exception:
                pass
    return report
