import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta

os.environ["PIXIV_DATA_DIR"] = tempfile.mkdtemp(prefix="pns_test_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Settings, load_settings, save_settings
from app.core.db import Database
from app.core.epub_exporter import build_epub
from app.core.md_index import render_author_md, render_illust_md
from app.core.models import Status
from app.core import paths
from app.pixiv.markup import convert_novel_markup
from app.pixiv.api import NovelMeta
from app.scheduler import DailyScheduler

failures = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        failures.append(name)


# 1. 标记转换
content = (
    "[chapter:序章]\n第一页正文，含[[rb:漢字>かんじ]]注音。\n[newpage]\n"
    "第二页，跳转到[jump:1]，插图：[pixivimage:12345]。\n未知标记[foo:bar]保留。"
)
res = convert_novel_markup(content, {"12345": "12345.jpg"})
check("分页生成 2 章", len(res.chapters) == 2)
check("chapter 转 h1", "<h1>序章</h1>" in res.chapters[0].body_xhtml)
check("ruby 转换", "<ruby>漢字<rt>かんじ</rt></ruby>" in res.chapters[0].body_xhtml)
check("jump 转锚点", 'href="page_001.xhtml"' in res.chapters[1].body_xhtml)
check("插图转 img", 'src="images/12345.jpg"' in res.chapters[1].body_xhtml)
check("未知标记保留", "[foo:bar]" in res.chapters[1].body_xhtml)
check("未知标记记录", res.unknown_markers == ["[foo:bar]"])
check("插图 ID 提取", res.illust_ids == ["12345"])

bad = convert_novel_markup("跳页[jump:9]越界")
check("越界 jump 保留原文", "[jump:9]" in bad.chapters[0].body_xhtml)

up = convert_novel_markup("插图[uploadedimage:abc1]。")
check("uploadedimage ID 提取", up.illust_ids == ["abc1"])
up2 = convert_novel_markup("插图[uploadedimage:abc1]。", {"abc1": "abc1.jpg"})
check("uploadedimage 转 img", 'src="images/abc1.jpg"' in up2.chapters[0].body_xhtml)

# 2. 路径规则
check("非法字符替换", paths.sanitize_name('a\\/:*?"<>|b.') == "a_________b")
check("超长截断", len(paths.sanitize_name("x" * 200)) == 120)
check("作者目录名", paths.author_dir_name("42", "小明/测试") == "42_小明_测试")
check("作品文件名", paths.novel_file_name("标题?", "99") == "标题_.99.epub")
check("命名仅标题", paths.novel_file_name("标题", "99", "{title}") == "标题.epub")
check("命名作者前缀", paths.novel_file_name("标题", "99", "{author}_{title}", "佐藤") == "佐藤_标题.epub")

from app.core.utils import format_datetime, clean_description
check("日期格式", format_datetime("2022-09-25T06:08:42+00:00") == "2022-09-25 06:08:42")
check("空日期", format_datetime("") == "")
check("简介清理", clean_description("第一行<br />第二<b>行</b>") == "第一行\n第二行")

_pdir = tempfile.mkdtemp()
_exist = os.path.join(_pdir, "同名.epub")
open(_exist, "w").close()
check("同名冲突加ID", paths.resolve_collision(_exist, {"novel_id": "7"}) == os.path.join(_pdir, "同名.7.epub"))
check("同名自身不冲突", paths.resolve_collision(_exist, {"novel_id": "7"}, _exist) == _exist)

# 3. 数据库
tmp = tempfile.mkdtemp()
db = Database(path=os.path.join(tmp, "t.db"))
check("schema 版本", db._conn.execute("PRAGMA user_version").fetchone()[0] == 6)
check("新增作者", db.add_author("100", "作者甲") is True)
check("重复作者拒绝", db.add_author("100") is False)
check("同 ID 可入插画名单", db.add_author("100", kind="illust") is True)
check("插画名单重复拒绝", db.add_author("100", kind="illust") is False)
check("名单按类型隔离", len(db.list_authors(kind="novel")) == 1 and len(db.list_authors(kind="illust")) == 1)
db.set_author_enabled("100", False, kind="illust")
check("分名单启停", db.get_author("100", "illust")["enabled"] == 0 and db.get_author("100", "novel")["enabled"] == 1)
db.remove_author("100", kind="illust")
check("分名单删除", db.get_author("100", "illust") is None and db.get_author("100", "novel") is not None)
db.upsert_novel({
    "novel_id": "5001", "author_id": "100", "author_name": "作者甲", "title": "旧标题",
    "status": Status.OK, "remote_updated_at": "2026-01-01T00:00:00", "local_path": r"C:\x\旧标题.5001.epub",
})
db.upsert_novel({"novel_id": "5001", "title": "新标题"})
rec = db.get_novel("5001")
check("upsert 局部更新保留路径", rec["title"] == "新标题" and rec["local_path"].endswith("5001.epub"))
db.set_novel_status("5001", Status.FAILED_NETWORK, "超时")
check("失败队列", len(db.failed_novels()) == 1)
db.set_novel_status("5001", Status.OK)
check("ok_novels 查询", [n["novel_id"] for n in db.ok_novels()] == ["5001"])
check("检索命中", len(db.search_novels("新标题")) == 1)
check("作者作品数", db.author_novel_count("100") == 1)

# 3b. 插画库
db.upsert_illust({
    "illust_id": "9001", "author_id": "100", "author_name": "作者甲", "title": "插图A",
    "page_count": 3, "status": Status.OK, "remote_updated_at": "2026-01-01T00:00:00",
    "publish_date": "2026-01-01T00:00:00+09:00", "local_path": r"C:\x\插图A.9001",
})
rec_i = db.get_illust("9001")
check("插画入库", rec_i is not None and rec_i["page_count"] == 3)
db.upsert_illust({"illust_id": "9001", "title": "插图A改"})
rec_i = db.get_illust("9001")
check("插画局部更新", rec_i["title"] == "插图A改" and rec_i["local_path"].endswith("9001"))
db.set_illust_status("9001", Status.FAILED_NETWORK, "超时")
check("插画失败队列", len(db.failed_illusts()) == 1)
db.set_illust_status("9001", Status.OK)
check("ok_illusts 查询", [i["illust_id"] for i in db.ok_illusts()] == ["9001"])
check("作者插画数", db.author_illust_count("100") == 1)
check("插画检索", len(db.search_illusts("插图A")) == 1)

db.upsert_novel({"novel_id": "6001", "author_id": "100", "author_name": "作者甲", "title": "系列A", "series_id": "13941263", "status": Status.OK})
check("系列轮询默认允许", db.should_poll_series("13941263") is True)
db.mark_series_deleted("13941263", "100")
check("系列 404 后标记跳过", db.should_poll_series("13941263") is False)
db.unmark_series_deleted("13941263")
check("系列恢复后允许轮询", db.should_poll_series("13941263") is True)

db.close()

# 3c. 插画路径规则
from app.core import paths as _paths
_i1 = {"illust_id": "9001", "author_id": "100", "author_name": "作者甲", "title": "单页图", "page_count": 1}
_i2 = {"illust_id": "9002", "author_id": "100", "author_name": "作者甲", "title": "多页图", "page_count": 5}
check("单页图路径为文件", _paths.illust_path(r"C:\img", _i1, ".png") == os.path.join(r"C:\img", "100_作者甲", "单页图.9001.png"))
check("多页图路径为文件夹", _paths.illust_path(r"C:\img", _i2) == os.path.join(r"C:\img", "100_作者甲", "多页图.9002"))
_s = Settings(epub_root=r"C:\epub")
check("图片目录默认跟随 EPUB", _paths.illust_root(_s) == os.path.join(r"C:\epub", "图片"))
_s.illust_root = r"D:\pic"
check("图片目录自定义", _paths.illust_root(_s) == r"D:\pic")
_s2 = Settings(epub_root=r"C:\epub", md_root=r"C:\md")
check("插画 MD 默认跟随小说 MD", _paths.author_illust_md_path(_s2, "100", "作者甲") == os.path.join(r"C:\md", "100_作者甲_插画.md"))
_s2.illust_md_root = r"D:\imd"
check("插画 MD 独立目录", _paths.author_illust_md_path(_s2, "100", "作者甲") == os.path.join(r"D:\imd", "100_作者甲_插画.md"))
check("小说 MD 不受影响", _paths.author_md_path(_s2, "100", "作者甲") == os.path.join(r"C:\md", "100_作者甲.md"))

# 3d. 插画 MD 独立索引
md3 = render_illust_md(
    {"author_id": "100", "author_name": "作者甲"},
    [{"illust_id": "9001", "title": "插图A", "status": "ok", "page_count": 3,
      "publish_date": "2026-01-01T00:00:00+09:00", "tags": "原创,风景"}],
    {"illust_new": 2, "illust_updated": 0, "illust_deleted": 0, "illust_failed": 0},
)
check("插画 MD 标题", md3.startswith("# 作者甲（插画·漫画）\n"))
check("插画 MD 分区", "## 作品" in md3)
check("插画 MD 链接", "### [插图A](https://www.pixiv.net/artworks/9001)" in md3)
check("插画 MD 页数", "- 页数：3" in md3)
check("插画 MD 收录统计", "- 收录插画/漫画：1 幅" in md3)
check("插画 MD 抓取统计", "新增 2 / 更新 0 / 远端删除 0 / 失败 0" in md3)
md3n = render_author_md({"author_id": "100", "author_name": "作者甲"}, [])
check("小说 MD 不含插画分区", "插画" not in md3n)

# 4. EPUB 构建
meta = NovelMeta(
    novel_id="5001", title="测试作品", author_id="100", author_name="作者甲",
    description="简介\n第二行", tags=["原创", "奇幻"], x_restrict=0,
    create_date="2026-01-01T00:00:00+09:00", upload_date="2026-02-01T00:00:00+09:00",
)
out = os.path.join(tmp, "out.epub")
build_epub(meta, res.chapters, {"12345.jpg": b"\xff\xd8\xff\xe0fakejpg"}, out, cover_bytes=b"\xff\xd8\xff\xe0cover")
check("EPUB 文件生成", os.path.getsize(out) > 1000)
with zipfile.ZipFile(out) as z:
    names = z.namelist()
    _info_name = "EPUB/info.xhtml" if "EPUB/info.xhtml" in names else "info.xhtml"
    _info = z.read(_info_name).decode("utf-8")
    _opf_name = "EPUB/content.opf" if "EPUB/content.opf" in names else "content.opf"
    _opf = z.read(_opf_name).decode("utf-8")
check("EPUB 含正文页", "EPUB/page_001.xhtml" in names or "page_001.xhtml" in names)
check("EPUB 含插图", any("12345.jpg" in n for n in names))
check("EPUB 含封面", any("cover" in n for n in names))
check("EPUB 封面页", any("cover.xhtml" in n for n in names))
check("EPUB 含信息页", any("info.xhtml" in n for n in names))
check("EPUB 含作品链接", "show.php?id=5001" in _info)
check("EPUB 日期本地化", "2026-01-01 00:00:00" in _info)
check("EPUB 封面为第一页", _opf.find("cover.xhtml") < _opf.find("info.xhtml"))

# 5. MD 索引（模板格式）
md = render_author_md(
    {"author_id": "100", "author_name": "作者甲"},
    [
        {"novel_id": "1", "title": "单篇A", "series_id": "", "tags": "原创,奇幻", "status": "ok",
         "publish_date": "2026-01-01T12:30:00+00:00", "remote_updated_at": "2026-01-02T08:00:00+00:00",
         "description": "第一段简介\n\n第二段简介<br />尾部"},
        {"novel_id": "2", "title": "系列一章", "series_id": "s1", "series_title": "系列S",
         "series_order": 1, "tags": "", "status": "deleted", "publish_date": "", "remote_updated_at": "", "description": ""},
    ],
)
check("MD 作者头", md.startswith("# 作者甲\n"))
check("MD 作者主页链接", "- Pixiv：https://www.pixiv.net/users/100" in md)
check("MD 收录统计", "- 收录作品：2 部" in md)
md2 = render_author_md(
    {"author_id": "100", "author_name": "作者甲"},
    [{"novel_id": "1", "title": "单篇A", "series_id": "", "tags": "", "status": "ok",
      "publish_date": "", "remote_updated_at": "", "description": ""}],
    {"new": 9, "updated": 1, "deleted": 2, "failed": 0, "series_deleted": 0},
)
check("MD 最近抓取统计", "- 最近抓取：" in md2 and "新增 9 / 更新 1 / 远端删除 2（单篇 2 / 系列 0）/ 失败 0" in md2)
check("MD 单篇标题行", "### [单篇A](https://www.pixiv.net/novel/show.php?id=1)" in md)
check("MD 链接行", "- 链接：https://www.pixiv.net/novel/show.php?id=1" in md)
check("MD 发布时间", "- 发布时间：2026-01-01 12:30" in md)
check("MD 更新时间", "- 更新时间：2026-01-02 08:00" in md)
check("MD 标签逗号分隔", "- 标签：原创, 奇幻" in md)
check("MD 简介引用块", "> 第一段简介\n>\n> 第二段简介\n> 尾部" in md)
check("MD 系列分区", "### 系列S" in md)
check("MD 系列条目四级标题", "#### [系列一章](https://www.pixiv.net/novel/show.php?id=2)（远端已删除）" in md)
md_del = render_author_md(
    {"author_id": "100", "author_name": "作者甲"},
    [{"novel_id": "2", "title": "系列一章", "series_id": "s1", "series_title": "系列S",
      "series_order": 1, "tags": "", "status": "ok", "publish_date": "", "remote_updated_at": "", "description": ""}],
    deleted_series={"s1"},
)
check("MD 系列删除标注", "### 系列S（系列已删除）" in md_del)
check("MD 开头已删除系列统计", "- 已删除系列：1 个" in md_del)
check("MD 未删系列无标注", "### 系列S\n" in md)
check("MD 未删系列无开头统计", "- 已删除系列" not in md)

# 5b. 命名迁移
_mig_dir = tempfile.mkdtemp()
_old_file = os.path.join(_mig_dir, "旧名.5001.epub")
open(_old_file, "w").close()
db3 = Database(path=os.path.join(_mig_dir, "m.db"))
db3.add_author("100", "作者甲")
db3.upsert_novel({
    "novel_id": "5001", "author_id": "100", "author_name": "作者甲", "title": "新标题",
    "status": Status.OK, "local_path": _old_file,
})
import logging
from app.core.scraper import ScraperEngine
_s = Settings(epub_root=_mig_dir, name_pattern="{title}")
_eng = ScraperEngine(_s, db3, "", logging.getLogger("t"))
_eng._migrate_filenames()
_new_file = os.path.join(_mig_dir, "100_作者甲", "单篇", "新标题.epub")
check("命名迁移重命名文件", os.path.exists(_new_file) and not os.path.exists(_old_file))
check("命名迁移更新索引", db3.get_novel("5001")["local_path"] == _new_file)
_eng.client.close()
db3.close()

# 5c. 昵称变更：旧 MD 文件清理
_md_dir = tempfile.mkdtemp()
db5 = Database(path=os.path.join(_md_dir, "m.db"))
db5.add_author("300", "旧昵称")
db5.upsert_novel({
    "novel_id": "6001", "author_id": "300", "author_name": "旧昵称", "title": "作品甲",
    "status": Status.OK, "local_path": os.path.join(_md_dir, "x.epub"),
})
_s5 = Settings(epub_root=_md_dir, name_pattern="{title}")
_eng5 = ScraperEngine(_s5, db5, "", logging.getLogger("t"))
_eng5._refresh_author_md("300")
_old_md = paths.author_md_path(_s5, "300", "旧昵称")
check("旧昵称 MD 已生成", os.path.exists(_old_md))
db5.update_author_name("300", "新昵称")
_eng5._refresh_author_md("300")
_new_md = paths.author_md_path(_s5, "300", "新昵称")
check("新昵称 MD 已生成", os.path.exists(_new_md))
check("旧昵称 MD 已清理", not os.path.exists(_old_md))
_eng5.client.close()
db5.close()

# 5d. 手动中止
from app.core.scraper import _AbortRun
db6 = Database(path=os.path.join(tempfile.mkdtemp(), "s.db"))
_eng6 = ScraperEngine(Settings(epub_root=tempfile.mkdtemp()), db6, "", logging.getLogger("t"))
try:
    _eng6._check_stop()
    check("未中止时不抛异常", True)
except _AbortRun:
    check("未中止时不抛异常", False)
_eng6.request_stop()
try:
    _eng6._check_stop()
    check("中止后抛出终止", False)
except _AbortRun:
    check("中止后抛出终止", True)
check("中止原因记录", "手动中止" in _eng6.report.aborted_reason)
import time as _time
_t0 = _time.monotonic()
_eng6.client._sleep(5.0)
check("中止后限速睡眠立即返回", _time.monotonic() - _t0 < 2.0)
_eng6.client.close()
db6.close()

# 6. 配置读写
s = Settings(epub_root=tmp, fetch_time="04:30", tag_blacklist=["R-18G"])
save_settings(s)
loaded = load_settings()
check("配置持久化", loaded.epub_root == tmp and loaded.fetch_time == "04:30" and loaded.tag_blacklist == ["R-18G"])

# 7. DPAPI 凭据
from app import credentials
credentials.save_cookie("PHPSESSID=test_session_123; other=1")
check("DPAPI 往返", credentials.load_cookie() == "PHPSESSID=test_session_123; other=1")
check("DPAPI 可用", credentials.dpapi_available())

# 7b. 备份导出导入
from app.core import backup
_bp = os.path.join(tmp, "backup.json")
_cur = load_settings()
backup.export_backup(_bp, _cur, [{"author_id": "200", "author_name": "作者乙", "enabled": 1}],
                     "PHPSESSID=backup_test")
db4 = Database(path=os.path.join(tmp, "b.db"))
_res = backup.import_backup(_bp, db4)
check("备份导入作者", _res["authors_added"] == 1 and db4.get_novel is not None)
check("备份恢复设置", load_settings().name_pattern == _cur.name_pattern)
check("备份恢复 Cookie", credentials.load_cookie() == "PHPSESSID=backup_test")
db4.close()
credentials.clear_cookie()

# 8. 调度器
sch = DailyScheduler("00:00")
check("当天首触发", sch.should_run() is True)
check("当天不重复", sch.should_run() is False)
sch2 = DailyScheduler("23:59")
check("未到时间不触发", sch2.should_run() is False)

# 9. 日志脱敏
from app.logging_setup import SensitiveFilter
import logging
record = logging.LogRecord("t", logging.INFO, "", 0, "cookie PHPSESSID=secret123 已使用", (), None)
SensitiveFilter().filter(record)
check("日志脱敏", "secret123" not in record.msg)

# 10. 作者状态统一判定
from app.ui.author_status import (AUTHOR_DELETED_LABEL, AUTHOR_STATUS_ACTIVE,
                                  AUTHOR_STATUS_DELETED, AUTHOR_STATUS_PENDING,
                                  AUTHOR_STATUS_UNKNOWN, author_display_name,
                                  resolve_author_status, status_text)
check("判定：停用视为已注销", resolve_author_status(
    {"author_id": "1", "author_name": "甲", "enabled": 0}) == AUTHOR_STATUS_DELETED)
check("判定：记录缺失视为已注销", resolve_author_status(None) == AUTHOR_STATUS_DELETED)
check("判定：ID 为空视为已注销", resolve_author_status(
    {"author_id": "", "author_name": "甲", "enabled": 1}) == AUTHOR_STATUS_DELETED)
check("判定：昵称缺失且非待校验视为已注销", resolve_author_status(
    {"author_id": "1", "author_name": "", "enabled": 1, "pending_validate": 0}) == AUTHOR_STATUS_DELETED)
check("判定：昵称缺失但待校验 → 待校验", resolve_author_status(
    {"author_id": "1", "author_name": "", "enabled": 1, "pending_validate": 1}) == AUTHOR_STATUS_PENDING)
check("判定：信息齐全 → 正常", resolve_author_status(
    {"author_id": "1", "author_name": "甲", "enabled": 1}) == AUTHOR_STATUS_ACTIVE)
check("判定：数据异常（字段类型错误）→ 未知", resolve_author_status(
    {"author_id": "1", "author_name": "甲", "enabled": "abc"}) == AUTHOR_STATUS_UNKNOWN)
check("判定：残缺对象（无字段）→ 兜底已注销",
      resolve_author_status(object()) == AUTHOR_STATUS_DELETED)
check("兜底：已注销且无昵称显示「已注销」",
      author_display_name({"author_id": "1", "author_name": "", "enabled": 0})
      == (AUTHOR_DELETED_LABEL, AUTHOR_STATUS_DELETED))
check("兜底：已注销且保留原昵称", author_display_name(
    {"author_id": "1", "author_name": "甲", "enabled": 0}) == ("甲（已注销）", AUTHOR_STATUS_DELETED))
check("兜底：任何输入都不产生空白显示名",
      all(author_display_name(x)[0] for x in
          [None, {}, {"author_id": "1", "author_name": "  ", "enabled": 1}]))
check("文案：状态列", status_text(AUTHOR_STATUS_DELETED) == "已注销"
      and status_text(AUTHOR_STATUS_PENDING) == "待校验"
      and status_text(AUTHOR_STATUS_UNKNOWN) == "信息加载失败"
      and status_text(AUTHOR_STATUS_ACTIVE) == "启用")

# 11. 已删除作品归集查询
db5 = Database(path=os.path.join(tmp, "del.db"))
db5.add_author("300", "作者丙", kind="novel")
db5.upsert_novel({"novel_id": "5001", "author_id": "300", "author_name": "作者丙",
                  "title": "作品甲", "status": "ok", "local_path": ""})
db5.set_novel_status("5001", "deleted")
db5.upsert_illust({"illust_id": "6001", "author_id": "300", "author_name": "作者丙",
                   "title": "图甲", "status": "ok", "local_path": ""})
db5.set_illust_status("6001", "deleted")
_dn = db5.deleted_novels()
_di = db5.deleted_illusts()
check("已删除作品：小说可查询", len(_dn) == 1 and _dn[0]["title"] == "作品甲")
check("已删除作品：插画可查询", len(_di) == 1 and _di[0]["illust_id"] == "6001")
check("已删除作品：已删除不计入收录数",
      db5.author_novel_count("300") == 0 and db5.author_deleted_novel_count("300") == 1)
db5.close()

# 12. 作者链接解析
from app.core.utils import extract_author_id
check("链接解析：纯数字 ID", extract_author_id("104854837") == "104854837")
check("链接解析：作者主页 URL",
      extract_author_id("https://www.pixiv.net/users/104854837") == "104854837")
check("链接解析：带子路径的 URL",
      extract_author_id("https://www.pixiv.net/users/104854837/novels") == "104854837")
check("链接解析：旧版 member.php",
      extract_author_id("https://www.pixiv.net/member.php?id=104854837") == "104854837")
check("链接解析：作品链接不给作者 ID",
      extract_author_id("https://www.pixiv.net/novel/show.php?id=123") == "")
check("链接解析：非法输入", extract_author_id("abc") == "" and extract_author_id("") == "")

# 13. 已注销作者的处理策略（不触网）
from app.core.scraper import ScraperEngine
_db6 = Database(path=os.path.join(tmp, "deact.db"))
_db6.add_author("7001", "甲", kind="novel")
_db6.add_author("7002", "乙", kind="novel")
_eng = ScraperEngine.__new__(ScraperEngine)
_eng.settings = Settings()
_eng.db = _db6
_eng.logger = logging.getLogger("t")
_eng.report = type("R", (), {"aborted_reason": ""})()
_eng.settings.author_404_action = "disable"
_eng._handle_author_missing("7001", {"author_id": "7001", "author_name": "甲"}, "novel")
check("注销策略 disable：自动停用", _db6.get_author("7001", "novel")["enabled"] == 0)
_eng.settings.author_404_action = "keep"
_eng._handle_author_missing("7002", {"author_id": "7002", "author_name": "乙"}, "novel")
check("注销策略 keep：保持启用", _db6.get_author("7002", "novel")["enabled"] == 1)
check("停用后 UI 判定为已注销",
      author_display_name(_db6.get_author("7001", "novel")) == ("甲（已注销）", AUTHOR_STATUS_DELETED))
_db6.close()

# 14. 区分「手动停用」与「账号已注销」
from app.core.db import DISABLE_REASON_DEACTIVATED, DISABLE_REASON_MANUAL
from app.ui.author_status import AUTHOR_STATUS_DISABLED
_db7 = Database(path=os.path.join(tmp, "reason.db"))
_db7.add_author("8001", "甲", kind="novel")
_db7.add_author("8002", "乙", kind="novel")
_db7.set_author_enabled("8001", False, kind="novel", reason=DISABLE_REASON_MANUAL)
_db7.set_author_enabled("8002", False, kind="novel", reason=DISABLE_REASON_DEACTIVATED)
check("手动停用：原因为 manual",
      _db7.author_disable_reason("8001", "novel") == DISABLE_REASON_MANUAL)
check("账号注销：原因为 deactivated",
      _db7.author_disable_reason("8002", "novel") == DISABLE_REASON_DEACTIVATED)
check("手动停用 → 显示「已停用」",
      author_display_name(_db7.get_author("8001", "novel")) == ("甲（已停用）", AUTHOR_STATUS_DISABLED))
check("账号注销 → 显示「已注销」",
      author_display_name(_db7.get_author("8002", "novel")) == ("乙（已注销）", AUTHOR_STATUS_DELETED))
check("状态列文案区分", status_text(AUTHOR_STATUS_DISABLED) == "已停用"
      and status_text(AUTHOR_STATUS_DELETED) == "已注销")
# 手动停用的不应被自动复查捞回
_old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S")
_db7._conn.execute("UPDATE authors SET disabled_at=? WHERE author_id IN ('8001','8002')", (_old,))
_db7._conn.commit()
_due = [a["author_id"] for a in _db7.authors_due_for_recheck(30, kind="novel")]
check("自动复查只捞回注销的，不含手动停用的", _due == ["8002"])
# 重新启用会清空原因
_db7.set_author_enabled("8001", True, kind="novel")
check("重新启用清空停用原因", _db7.author_disable_reason("8001", "novel") == "")
_db7.close()

# 15. 离线重建 MD 索引
from app.core.scraper import rebuild_md_indexes, refresh_author_md
_md_root = os.path.join(tmp, "mdrebuild")
_db8 = Database(path=os.path.join(tmp, "md.db"))
_db8.add_author("9001", "作者甲", kind="novel")
_db8.add_author("9001", "作者甲", kind="illust")
_db8.upsert_novel({"novel_id": "9501", "author_id": "9001", "author_name": "作者甲",
                   "title": "作品一", "status": "ok", "local_path": "a.epub",
                   "publish_date": "2026-01-01T10:00:00"})
_s2 = Settings()
_s2.epub_root = _md_root
rep = rebuild_md_indexes(_s2, _db8, logging.getLogger("t"))
check("重建索引：仅按作者去重统计", rep["authors"] == 1 and rep["written"] == 1)
_written = [f for f in os.listdir(os.path.join(_md_root, "9001_作者甲")) if f.endswith(".md")]
check("重建索引：生成了 MD 文件", len(_written) >= 1)
# 关闭开关后，抓取流程不写、但显式重建仍可写
_s2.write_md_index = False
check("重建索引不受 write_md_index 开关影响（显式操作）",
      refresh_author_md(_s2, _db8, logging.getLogger("t"), "9001") is True)
_db8.close()

# 16. 检索与队列单条操作
_db9 = Database(path=os.path.join(tmp, "ops.db"))
_db9.add_author("9101", "作者庚", kind="novel")
_db9.upsert_novel({"novel_id": "9601", "author_id": "9101", "author_name": "作者庚",
                   "title": "关键字小说", "status": "ok", "local_path": "n.epub"})
_db9.upsert_illust({"illust_id": "9701", "author_id": "9101", "author_name": "作者庚",
                    "title": "关键字插画", "status": "ok", "local_path": "i.jpg"})
check("检索：小说可被查到", len(_db9.search_novels("关键字")) == 1)
check("检索：插画可被查到（此前 UI 未接此接口）", len(_db9.search_illusts("关键字")) == 1)
_db9.set_novel_status("9601", "deleted")
check("队列：已删除作品可查询", len(_db9.deleted_novels()) == 1)
_db9.remove_novel("9601")
check("队列：移除小说记录后查不到", _db9.get_novel("9601") is None)
_db9.remove_illust("9701")
check("队列：移除插画记录后查不到", _db9.get_illust("9701") is None)
_db9.close()

# 17. 抓取前自动备份（按用户要求：包含 Cookie）
from app.core.backup import auto_backup_dir, create_auto_backup
from app.core.scraper import RunReport, ScraperEngine
_db10 = Database(path=os.path.join(tmp, "ab.db"))
_db10.add_author("9201", "作者壬", kind="novel")
_s3 = Settings()
_ab_dir = auto_backup_dir()
check("RunReport 默认 auth_expired=False", RunReport().auth_expired is False)

_before = len([f for f in os.listdir(_ab_dir)]) if os.path.isdir(_ab_dir) else 0
_eng3 = ScraperEngine.__new__(ScraperEngine)
_eng3.settings = Settings()
_eng3.settings.auto_backup_enabled = False
_eng3.db = _db10
_eng3.cookie = "PHPSESSID=x"
_eng3.logger = logging.getLogger("t")
_eng3._auto_backup()
_after = len([f for f in os.listdir(_ab_dir)]) if os.path.isdir(_ab_dir) else 0
check("自动备份：关闭时不产生备份", _before == _after)

_p = create_auto_backup(_s3, _db10, "PHPSESSID=auto_backup_test", keep=3)
check("自动备份：文件已生成", os.path.exists(_p))
_raw = json.load(open(_p, encoding="utf-8"))
check("自动备份：包含 Cookie（按用户要求）", _raw.get("cookie") == "PHPSESSID=auto_backup_test")
check("自动备份：包含作者列表", len(_raw.get("authors", [])) == 1)
for _n in ("backup_20260101_000000.json", "backup_20260102_000000.json",
           "backup_20260103_000000.json"):
    with open(os.path.join(_ab_dir, _n), "w", encoding="utf-8") as _fh:
        _fh.write("{}")
create_auto_backup(_s3, _db10, "PHPSESSID=x", keep=3)
_left = sorted(f for f in os.listdir(_ab_dir)
               if f.startswith("backup_") and f.endswith(".json"))
check("自动备份：滚动保留 3 份", len(_left) == 3)
_db10.close()

print()
if failures:
    print("失败项：", failures)
    sys.exit(1)
print("全部通过")
