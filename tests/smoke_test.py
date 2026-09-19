import os
import sys
import tempfile
import zipfile

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
check("schema 版本", db._conn.execute("PRAGMA user_version").fetchone()[0] == 3)
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
    {"new": 9, "updated": 1, "deleted": 2, "failed": 0},
)
check("MD 最近抓取统计", "- 最近抓取：" in md2 and "新增 9 / 更新 1 / 远端删除 2 / 失败 0" in md2)
check("MD 单篇标题行", "### [单篇A](https://www.pixiv.net/novel/show.php?id=1)" in md)
check("MD 链接行", "- 链接：https://www.pixiv.net/novel/show.php?id=1" in md)
check("MD 发布时间", "- 发布时间：2026-01-01 12:30" in md)
check("MD 更新时间", "- 更新时间：2026-01-02 08:00" in md)
check("MD 标签逗号分隔", "- 标签：原创, 奇幻" in md)
check("MD 简介引用块", "> 第一段简介\n>\n> 第二段简介\n> 尾部" in md)
check("MD 系列分区", "### 系列S" in md)
check("MD 系列条目四级标题", "#### [系列一章](https://www.pixiv.net/novel/show.php?id=2)（远端已删除）" in md)

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

print()
if failures:
    print("失败项：", failures)
    sys.exit(1)
print("全部通过")
