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
from . import paths
from .db import Database
from .epub_exporter import build_epub
from .md_index import write_author_md
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
    aborted_reason: str = ""
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
        self.report = RunReport()

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
        root = self.settings.epub_root
        if not root:
            return False
        drive = os.path.splitdrive(os.path.abspath(root))[0] + os.sep
        free_mb = shutil.disk_usage(drive).free / (1024 * 1024)
        return free_mb >= self.settings.disk_threshold_mb

    def run(self) -> RunReport:
        self.report.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.logger.info("任务开始")
        try:
            if not self._check_disk():
                self.report.aborted_reason = f"磁盘剩余空间不足 {self.settings.disk_threshold_mb} MB，任务暂停"
                self.logger.warning(self.report.aborted_reason)
                return self.report
            try:
                self._migrate_filenames()
                self._retry_failed_queue()
                authors = self.db.list_authors(enabled_only=True)
                for author in authors:
                    self._guard_failures()
                    self._process_author(author)
            except AuthExpiredError:
                self.report.aborted_reason = "登录已失效，请重新导入 Cookie"
                self.logger.error(self.report.aborted_reason)
            except _AbortRun:
                pass
        finally:
            self.client.close()
            self.report.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(
                "任务结束：新增 %d，更新 %d，跳过 %d，失败 %d",
                self.report.new_count, self.report.updated_count,
                self.report.skipped_count, self.report.failed_count,
            )
        return self.report

    def _guard_failures(self):
        self._check_stop()
        limit = max(1, int(self.settings.max_consecutive_failures))
        if self.client.consecutive_failures >= limit:
            self.report.aborted_reason = f"连续失败 {limit} 次，任务自动暂停，请检查网络与代理"
            self.logger.error(self.report.aborted_reason)
            raise _AbortRun()

    def _retry_failed_queue(self):
        queue = [n for n in self.db.failed_novels() if n["status"] != Status.FAILED_AUTH]
        if not queue:
            return
        self.logger.info("优先重试失败队列：%d 部", len(queue))
        for novel in queue:
            self._guard_failures()
            self._process_novel(novel["novel_id"], novel["author_id"], is_retry=True)

    def _process_author(self, author: dict):
        aid = author["author_id"]
        stat = {"new": 0, "updated": 0, "skipped": 0, "failed": 0, "deleted": 0}
        self.report.per_author[aid] = stat
        self._emit("author_start", author_id=aid, author_name=author.get("author_name") or aid)
        try:
            if author.get("pending_validate") or not author.get("author_name"):
                try:
                    name = api.get_user_name(self.client, aid)
                    if name:
                        self.db.update_author_name(aid, name)
                        author["author_name"] = name
                except Exception:
                    self.logger.warning("作者 %s 昵称补拉失败", aid, exc_info=True)
            remote_ids = set(api.get_user_novel_ids(self.client, aid))
        except AuthExpiredError:
            raise
        except PixivRequestError as e:
            if e.kind == "not_found":
                self.db.set_author_enabled(aid, False)
                self.logger.warning(
                    "作者 %s（%s）账号已注销或不存在，已自动停用，本地作品保留",
                    aid, author.get("author_name") or "",
                )
            else:
                stat["failed"] += 1
                self.report.failed_count += 1
                self.logger.error("作者 %s 作品列表获取失败：%s", aid, e)
            return
        except Exception as e:
            stat["failed"] += 1
            self.report.failed_count += 1
            self.logger.error("作者 %s 作品列表获取失败：%s", aid, e)
            return

        local_map = {n["novel_id"]: n for n in self.db.novels_by_author(aid)}
        for nid, rec in local_map.items():
            if nid not in remote_ids and rec["status"] in (Status.OK, Status.OUTDATED):
                self.db.set_novel_status(nid, Status.DELETED)
                self.report.deleted_count += 1
                stat["deleted"] += 1
                self.logger.info("作品 %s 远端已删除，本地保留", nid)

        remote_name = ""
        missing_files = [
            nid for nid, rec in local_map.items()
            if rec["status"] == Status.OK and not (rec.get("local_path") and os.path.exists(rec["local_path"]))
        ]
        if missing_files:
            self.logger.info("作者 %s 有 %d 部作品本地文件缺失，重新下载", aid, len(missing_files))
        queue = sorted(
            set(missing_files) | {nid for nid in remote_ids if nid not in local_map},
            key=int,
        )
        for nid in queue:
            self._guard_failures()
            name = self._process_novel(nid, aid, stat=stat)
            if name and not remote_name:
                remote_name = name

        for series_id in self.db.series_ids_by_author(aid):
            self._guard_failures()
            try:
                series = api.get_series_novel_ids(self.client, series_id)
            except Exception as e:
                self.logger.warning("系列 %s 轮询失败：%s", series_id, e)
                continue
            for nid in series.novel_ids:
                rec = self.db.get_novel(nid)
                if rec is None or rec["status"] == Status.OUTDATED:
                    name = self._process_novel(nid, aid, stat=stat)
                    if name and not remote_name:
                        remote_name = name

        if remote_name and remote_name != (author.get("author_name") or ""):
            self.logger.info("作者 %s 昵称变更：%s -> %s，迁移文件至新目录", aid, author.get("author_name"), remote_name)
            self.db.update_author_name(aid, remote_name)
            self._migrate_filenames()

        self.db.touch_author_fetch(aid)
        self._refresh_author_md(aid, stat)
        self.report.new_count += stat["new"]
        self.report.updated_count += stat["updated"]
        self.report.skipped_count += stat["skipped"]
        self.report.failed_count += stat["failed"]
        self._emit("author_done", author_id=aid, **stat)

    def _migrate_filenames(self):
        for rec in self.db.ok_novels():
            old = rec.get("local_path") or ""
            if not old or not os.path.exists(old):
                continue
            desired = paths.novel_path(self.settings.epub_root, rec, self.settings.name_pattern)
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

    def _process_novel(self, novel_id: str, author_id: str, is_retry: bool = False, stat: dict = None):
        stat = stat if stat is not None else {"new": 0, "updated": 0, "skipped": 0, "failed": 0}
        try:
            meta, content = api.get_novel_detail(self.client, novel_id)
        except PixivRequestError as e:
            if e.kind == "not_found":
                self.db.set_novel_status(novel_id, Status.DELETED)
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

        if existing and existing["status"] == Status.OK:
            remote_ts = meta.upload_date
            local_ts = existing.get("remote_updated_at") or ""
            local_file = existing.get("local_path") or ""
            file_exists = bool(local_file) and os.path.exists(local_file)
            if remote_ts and local_ts and remote_ts <= local_ts and file_exists:
                stat["skipped"] += 1
                return meta.author_name

        is_update = existing is not None
        try:
            converted = convert_novel_markup(content)
            if converted.unknown_markers:
                self.logger.warning("作品 %s 含未识别标记：%s", novel_id, ", ".join(converted.unknown_markers[:5]))
            illust_files = self._fetch_illusts(novel_id, converted.illust_ids, meta.embedded_images)
            if illust_files:
                markup_map = {iid: fn for iid, (fn, _blob) in illust_files.items()}
                converted = convert_novel_markup(content, markup_map)
            images = {fn: blob for fn, blob in illust_files.values()} if illust_files else {}
            cover = None
            cover_name = "cover.jpg"
            if meta.cover_url:
                try:
                    cover = api.download_image(self.client, meta.cover_url)
                    cover_name = os.path.basename(meta.cover_url.split("?")[0]) or "cover.jpg"
                except Exception:
                    self.logger.warning("作品 %s 封面下载失败", meta.cover_url, exc_info=True)
            if cover is None and images:
                cover_name, cover = next(iter(images.items()))
                self.logger.info("作品 %s 无封面，使用第一张插图作为封面", novel_id)

            rec = self._index_novel(meta, existing, Status.OK)
            out_path = paths.novel_path(self.settings.epub_root, rec, self.settings.name_pattern)
            old_path = existing.get("local_path") if existing else ""
            out_path = paths.resolve_collision(out_path, rec, old_path)
            build_epub(meta, converted.chapters, images, out_path, cover, cover_name)
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
        except AuthExpiredError:
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

    def _refresh_author_md(self, author_id: str, run_stat: dict = None):
        author = next((a for a in self.db.list_authors() if a["author_id"] == author_id), None)
        if not author:
            return
        novels = self.db.novels_by_author(author_id)
        path = paths.author_md_path(self.settings, author_id, author.get("author_name") or "")
        try:
            write_author_md(path, author, novels, run_stat)
        except Exception:
            self.logger.warning("作者 %s MD 索引写入失败", author_id, exc_info=True)
            return
        cleanup_dirs = {os.path.dirname(path)}
        if self.settings.md_separate and self.settings.md_root:
            cleanup_dirs.add(self.settings.md_root)
        if os.path.isdir(self.settings.epub_root):
            for d in os.listdir(self.settings.epub_root):
                full_dir = os.path.join(self.settings.epub_root, d)
                if d.startswith(f"{author_id}_") and os.path.isdir(full_dir):
                    cleanup_dirs.add(full_dir)
        new_abs = os.path.abspath(path)
        for d in cleanup_dirs:
            if not os.path.isdir(d):
                continue
            for fname in os.listdir(d):
                full = os.path.join(d, fname)
                if (fname.startswith(f"{author_id}_") and fname.endswith(".md")
                        and os.path.abspath(full) != new_abs):
                    try:
                        os.remove(full)
                        self.logger.info("清理旧昵称 MD 文件：%s", fname)
                    except OSError:
                        pass


class _AbortRun(Exception):
    pass
