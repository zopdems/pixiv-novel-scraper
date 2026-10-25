"""分批抓取（单轮下载上限）的单元测试。"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("PIXIV_DATA_DIR", tempfile.mkdtemp(prefix="pixiv_batch_"))

from app.core.scraper import _BatchLimitReached, ScraperEngine  # noqa: E402
from app.config import Settings  # noqa: E402


def _engine(batch_limit):
    s = Settings()
    s.batch_limit = batch_limit
    # 不真的初始化 PixivClient / 数据库，直接构造一个最小对象测计数逻辑
    eng = ScraperEngine.__new__(ScraperEngine)
    eng.settings = s
    eng._batch_downloaded = 0
    eng._batch_limit = batch_limit
    eng.dry_run = False
    eng.dry_run_would_download = 0
    return eng


def test_no_limit_never_raises():
    eng = _engine(0)
    for _ in range(1000):
        eng._register_download()  # 不应抛异常


def test_limit_raises_exactly_at_boundary():
    eng = _engine(3)
    eng._register_download()  # 1
    eng._register_download()  # 2
    try:
        eng._register_download()  # 3 → 抛
        raised = False
    except _BatchLimitReached:
        raised = True
    assert raised


def test_limit_below_boundary_ok():
    eng = _engine(5)
    for _ in range(4):
        eng._register_download()  # 4/5，未到
    assert eng._batch_downloaded == 4


def test_report_has_truncated_flag():
    from app.core.scraper import RunReport
    r = RunReport()
    assert r.truncated is False


def test_dry_run_flag_and_counter():
    eng = _engine(0)
    assert eng.dry_run is False
    assert eng.dry_run_would_download == 0
    eng.dry_run = True
    eng.dry_run_would_download += 1
    assert eng.dry_run_would_download == 1
