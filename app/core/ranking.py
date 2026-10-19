"""小说排行榜抓取与正文下载调度。

分层策略（关键设计）：
- 元信息层：每轮都完整记录名次与收藏数，这是排行榜的价值所在，丢一天就断档；
- 正文层：默认只在「首次上榜 / 本地缺失」时下载。一部作品霸榜 30 天时，
  第 2~30 天对正文发 0 次请求，避免重复下载与风控风险。

正文下载复用 ScraperEngine 的单篇管线（_process_novel），
因此 EPUB 生成、目录组织、更新判定、标签过滤、R-18 开关等行为与主动订阅完全一致。
"""
import datetime
import os
import re
import threading
import time

from ..config import app_data_dir
from ..pixiv.client import (AuthExpiredError, PixivClient, PixivRequestError,
                            RiskControlError)
from .rank_db import (LANG_ZH_CN, SRC_DOWNLOAD, SRC_MANUAL, RankDatabase,
                      mode_label as mode_label_of)
from .scraper import ScraperEngine

RANK_API = "https://www.pixiv.net/ajax/ranking/novel"
PAGE_SIZE = 50      # 实测每页固定 50 条
TOP_N = 100         # 前一百名 = 2 页；第 3 页会 404
MAX_PAGES = TOP_N // PAGE_SIZE


def _parse_api_date(value) -> str:
    """解析接口返回的日期，无法识别时返回空串。

    接口会返回「2026年9月1日」这种月/日不带前导零的格式，
    直接抽数字只会得到 202691（6 位），必须按分隔符拆开再补零。
    """
    s = str(value or "").strip()
    m = re.match(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def _norm_date(value) -> str:
    """把任意日期表示统一成 YYYY-MM-DD；无法识别则返回今天。"""
    parsed = _parse_api_date(value)
    return parsed or time.strftime("%Y-%m-%d")


def normalize(item: dict) -> dict:
    """把接口原始条目归一化成入库结构。"""
    tags = item.get("tag_a") or []
    if isinstance(tags, list):
        tags = [str(t) for t in tags if str(t).strip()]
    else:
        tags = []
    return {
        "work_id": str(item.get("id") or "").strip(),
        "rank": int(item.get("rank") or 0),
        "title": str(item.get("title") or ""),
        "author_id": str(item.get("user_id") or "").strip(),
        "author_name": str(item.get("user_name") or ""),
        "series_id": str(item.get("series_id") or "").strip(),
        "series_title": str(item.get("series_title") or ""),
        "word_count": int(item.get("word_count") or 0),
        "reading_time": int(item.get("reading_time") or 0),
        "bookmark_count": int(item.get("bookmark_count") or 0),
        "x_restrict": int(item.get("x_restrict") or 0),
        "ai_type": int(item.get("ai_type") or 0),
        "genre": str(item.get("genre") or ""),
        "tags": tags,
        "cover_url": str(item.get("url") or ""),
        "language": str(item.get("language") or "").strip().lower(),
    }


def filter_language(items: list, language: str) -> list:
    """按语言过滤榜单条目；language 为空则不过滤。

    接口 language 的实际取值：ja / zh-cn / other。
    """
    if not language:
        return list(items)
    want = str(language).strip().lower()
    return [x for x in items if (x.get("language") or "").lower() == want]


def fetch_ranking(client: PixivClient, mode: str, logger=None,
                  date: str = None, top_n: int = TOP_N):
    """抓取指定榜单的前 N 名，返回 (归一化列表, 榜单日期)。

    mode 无效时接口会返回空 body（rookie）或 400（original），此处按空结果处理并告警。
    """
    items = []
    rank_date = ""
    requested_date = _norm_date(date) if date else ""
    pages = max(1, (top_n + PAGE_SIZE - 1) // PAGE_SIZE)
    for page in range(1, pages + 1):
        url = f"{RANK_API}?mode={mode}&p={page}"
        if date:
            url += f"&date={str(date).replace('-', '')}"
        data = client.get_json(url)
        body = data.get("body") or {}
        if not rank_date:
            # 只有解析成功才采纳；失败时留给下面回退到「请求的日期」
            rank_date = _parse_api_date(body.get("date"))
        arr = ((body.get("display_a") or {}).get("rank_a")) or []
        if not arr:
            break
        # 未登录时部分条目会被遮蔽成字符串占位（时有时无），跳过，避免解析崩溃
        items.extend([x for x in arr if isinstance(x, dict)])
        if len(arr) < PAGE_SIZE:
            break
    if not items and logger:
        logger.warning("榜单 %s 未取到数据（可能不支持该榜单或当日无数据）", mode)
    # 回退顺序：接口给的日期 → 你请求的日期 → 今天
    return [normalize(x) for x in items][:top_n], \
        rank_date or requested_date or time.strftime("%Y-%m-%d")


class RankingService:
    """排行榜抓取服务：抓元信息入库 + 按需下载正文。"""

    def __init__(self, settings, db, rank_db: RankDatabase, cookie: str,
                 logger, progress_cb=None):
        self.settings = settings
        self.db = db
        self.rank_db = rank_db
        self.cookie = cookie or ""
        self.logger = logger
        self.progress_cb = progress_cb or (lambda event: None)
        self._stop = threading.Event()
        self.client = PixivClient(settings, self.cookie, logger, stop_event=self._stop)

    def request_stop(self):
        self._stop.set()

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass

    # ------------------------------------------------------------ 下载判定
    def _need_download(self, work_id: str) -> bool:
        """本地库挡一道：已下载且文件还在的，直接跳过（连详情请求都不发）。"""
        rec = self.db.get_novel(work_id)
        if rec is None or rec.get("status") != "ok":
            return True
        path = rec.get("local_path") or ""
        return not (path and os.path.exists(path))

    # ------------------------------------------------------------ 主流程
    def run(self, modes: list, download: bool = True,
            download_mode: str = "new", date: str = None, top_n: int = TOP_N,
            source: str = "") -> dict:
        """抓取某一天的排行榜并按需下载正文。"""
        t0 = time.time()
        try:
            rep = self._run_once(modes, download=download,
                                 download_mode=download_mode, date=date, top_n=top_n)
        finally:
            self.close()
        self._log_run(rep, source, modes, 1, t0)
        return rep

    def backfill(self, modes: list, days: int, download: bool = True,
                 download_mode: str = "new", top_n: int = TOP_N,
                 source: str = "") -> dict:
        """往前补抓最近 N 天的榜单（含今天），逐天执行，可中断。"""
        report = {
            "modes": {}, "collected": 0, "targets": 0,
            "downloaded": 0, "skipped": 0, "failed": 0,
            "errors": [], "rank_date": "", "date_from": "", "auth_expired": False,
            "filtered_out": 0, "csv": [], "days": [], "days_ok": 0,
        }
        today = datetime.date.today()
        t0 = time.time()
        try:
            for i in range(days - 1, -1, -1):
                if self._stop.is_set():
                    break
                d = today - datetime.timedelta(days=i)
                stamp = d.strftime("%Y%m%d")
                self.progress_cb({"tag": "rank_backfill", "date": d.isoformat(),
                                  "index": days - i, "total": days})
                day = self._run_once(modes, download=download,
                                     download_mode=download_mode, date=stamp, top_n=top_n)
                report["days"].append({"date": d.isoformat(), "collected": day["collected"]})
                if day["collected"]:
                    report["days_ok"] += 1
                # 补抓是从旧到新跑的：首日即起始日，最后一天即最新榜单日
                if not report["date_from"]:
                    report["date_from"] = d.isoformat()
                report["rank_date"] = day.get("rank_date") or d.isoformat()
                report["collected"] += day["collected"]
                report["downloaded"] += day["downloaded"]
                report["skipped"] += day["skipped"]
                report["failed"] += day["failed"]
                report["filtered_out"] += day["filtered_out"]
                report["csv"] += day["csv"]
                for k, v in day["modes"].items():
                    report["modes"][k] = report["modes"].get(k, 0) + v
                if day["auth_expired"]:
                    report["auth_expired"] = True
                    break
                for e in day["errors"]:
                    report["errors"].append(f"{d.isoformat()}：{e}")
                if i:   # 天与天之间留一点间隔，别把接口打太急
                    time.sleep(1)
        finally:
            self.close()
        self._log_run(report, source, modes, len(report["days"]) or days, t0)
        return report

    def _log_run(self, report: dict, source: str, modes: list, days: int, t0: float):
        """把本轮结果写进抓取历史（rank_runs）。

        这是「每天抓了多少」的唯一持久化来源，但它是附带的记账行为：
        写库失败只告警，绝不能把已经抓到的数据弄丢。
        """
        try:
            errs = [str(e) for e in (report.get("errors") or [])]
            days_ok = report.get("days_ok")
            if not days_ok:
                days_ok = 1 if report.get("collected") else 0
            payload = dict(
                started_at=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
                rank_date=str(report.get("rank_date") or ""),
                date_from=str(report.get("date_from") or report.get("rank_date") or ""),
                source=source or SRC_MANUAL,
                modes=",".join(modes or []),
                days=int(days or 0),
                days_ok=int(days_ok or 0),
                collected=int(report.get("collected") or 0),
                downloaded=int(report.get("downloaded") or 0),
                skipped=int(report.get("skipped") or 0),
                failed=int(report.get("failed") or 0),
                filtered_out=int(report.get("filtered_out") or 0),
                csv_count=len(report.get("csv") or []),
                elapsed=round(time.time() - t0, 1),
                ok=not (report.get("auth_expired") or errs),
                message="；".join(errs),
            )
            report["run_id"] = self.rank_db.add_run(**payload)
            # 人能直接看的那一份：跟 run.log 放一起，双击就能翻
            if getattr(self.settings, "ranking_txt_log", True):
                self.rank_db.append_run_txt(**payload)
        except Exception as e:
            self.logger.warning("抓取历史写入失败（不影响本次结果）：%s", e)

    def _run_once(self, modes: list, download: bool = True,
                  download_mode: str = "new", date: str = None, top_n: int = TOP_N) -> dict:
        """抓一天（内部用，不关闭 client，便于补抓时复用连接）。

        download_mode:
          new            —— 仅下载「首次上榜 / 本地缺失」的作品（默认，最省请求）
          follow_update  —— 每篇都走单篇管线，由 update_mode 设置决定是否重下
        """
        report = {
            "modes": {}, "collected": 0, "targets": 0,
            "downloaded": 0, "skipped": 0, "failed": 0,
            "errors": [], "rank_date": "", "auth_expired": False,
            "filtered_out": 0, "csv": [],
        }
        targets = {}   # work_id -> author_id（跨榜去重）
        # 语言过滤：默认只保留简体中文（R-18 榜同样处理）
        lang = LANG_ZH_CN if getattr(self.settings, "ranking_language_filter", True) else ""
        for mode in modes:
            if self._stop.is_set():
                break
            self.progress_cb({"tag": "rank_start", "mode": mode})
            try:
                items, rank_date = fetch_ranking(
                    self.client, mode, self.logger, date=date, top_n=top_n)
            except AuthExpiredError:
                report["auth_expired"] = True
                report["errors"].append(f"{mode}：登录已失效")
                break
            except RiskControlError as e:
                report["errors"].append(f"{mode}：被风控拦截（{e}）")
                continue
            except PixivRequestError as e:
                report["errors"].append(f"{mode}：{e}")
                continue
            if not items:
                # 无登录 Cookie 时，男性向 / 女性向榜的条目会被遮蔽成 '0','1','2'… 占位；
                # 而当天/未来的榜单则是还没公布，两种情况要分开说清楚
                if date and _norm_date(date) >= time.strftime("%Y-%m-%d"):
                    report["errors"].append(f"{mode_label_of(mode)}榜当天榜单尚未公布")
                else:
                    report["errors"].append(
                        f"{mode_label_of(mode)}榜未取到数据（条目被遮蔽，"
                        f"可能需要登录 Cookie；R-18 榜无 Cookie 会直接 403）")
                self.logger.warning("榜单 %s 未取到可用条目", mode)
                continue
            total = len(items)
            if lang:
                items = filter_language(items, lang)
                report["filtered_out"] += total - len(items)
            if not items:
                report["errors"].append(
                    f"{mode_label_of(mode)}榜当日无简体中文作品（已过滤 {total} 条）")
                self.logger.info("榜单 %s 过滤后为空（原始 %d 条）", mode, total)
                continue
            report["rank_date"] = report["rank_date"] or rank_date
            saved = self.rank_db.save_snapshot(mode, rank_date, items)
            report["modes"][mode] = saved
            report["collected"] += saved
            csv_path = self._write_csv(mode, rank_date)
            if csv_path:
                report["csv"].append(csv_path)
            for it in items:
                if it["work_id"]:
                    targets.setdefault(it["work_id"], it["author_id"])
            self.logger.info("榜单 %s（%s）取到 %d 条", mode, rank_date, saved)

        report["targets"] = len(targets)
        if download and targets and not self._stop.is_set():
            self._download(targets, report, download_mode)
        return report

    # ------------------------------------------------------------ CSV
    def _write_csv(self, mode: str, rank_date: str) -> str:
        """每次抓取成功后在指定目录自动生成一份 CSV（同日重复抓取会覆盖）。"""
        if not getattr(self.settings, "ranking_csv_enabled", True):
            return ""
        base = (getattr(self.settings, "ranking_csv_dir", "") or "").strip()
        if not base:
            base = os.path.join(app_data_dir(), "rank_csv")
        try:
            os.makedirs(base, exist_ok=True)
            path = os.path.join(base, f"排行榜_{mode}_{rank_date}.csv")
            self.rank_db.export_csv(mode, rank_date, path)
            return path
        except Exception as e:
            self.logger.warning("榜单 CSV 写入失败：%s", e)
            return ""

    def download_works(self, works: dict, source: str = "") -> dict:
        """只下载指定作品（手动补下载用），不走「仅首次上榜」过滤。

        works: {work_id: author_id}
        """
        report = {"modes": {}, "collected": 0, "targets": len(works),
                  "downloaded": 0, "skipped": 0, "failed": 0,
                  "errors": [], "rank_date": "", "auth_expired": False}
        t0 = time.time()
        try:
            self._download(works, report, "follow_update")
        finally:
            self.close()
        self._log_run(report, source or SRC_DOWNLOAD, [], 0, t0)
        return report

    def _download(self, targets: dict, report: dict, download_mode: str):
        engine = ScraperEngine(self.settings, self.db, self.cookie, self.logger,
                               progress_cb=self.progress_cb)
        try:
            for work_id, author_id in targets.items():
                if self._stop.is_set():
                    break
                if download_mode != "follow_update" and not self._need_download(work_id):
                    report["skipped"] += 1
                    continue
                before = self.db.get_novel(work_id)
                try:
                    engine._process_novel(work_id, author_id)
                except AuthExpiredError:
                    report["auth_expired"] = True
                    report["errors"].append("登录已失效，正文下载中止")
                    break
                except Exception as e:
                    report["failed"] += 1
                    self.logger.warning("作品 %s 下载失败：%s", work_id, e)
                    continue
                after = self.db.get_novel(work_id)
                is_new = (before is None or before.get("status") != "ok") and \
                    after and after.get("status") == "ok"
                if is_new:
                    report["downloaded"] += 1
                    self.progress_cb({"tag": "rank_downloaded", "work_id": work_id})
                else:
                    report["skipped"] += 1
        finally:
            engine.client.close()
