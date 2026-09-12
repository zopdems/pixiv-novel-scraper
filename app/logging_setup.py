import logging
import os
import re
from logging.handlers import TimedRotatingFileHandler

from .config import app_data_dir


def log_dir() -> str:
    path = os.path.join(app_data_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


class SensitiveFilter(logging.Filter):
    PATTERNS = [
        re.compile(r"(PHPSESSID=)[^;\s]+", re.IGNORECASE),
        re.compile(r'(Authorization["\']?\s*[:=]\s*["\']?)[^"\'\s,]+', re.IGNORECASE),
        re.compile(r'("password"\s*:\s*")[^"]+', re.IGNORECASE),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            for pat in self.PATTERNS:
                msg = pat.sub(r"\1***", msg)
            record.msg = msg
            record.args = ()
        except Exception:
            pass
        return True


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("pixiv_scraper")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    run_handler = TimedRotatingFileHandler(
        os.path.join(log_dir(), "run.log"), when="midnight", backupCount=30, encoding="utf-8"
    )
    run_handler.setFormatter(fmt)
    run_handler.addFilter(SensitiveFilter())

    err_handler = TimedRotatingFileHandler(
        os.path.join(log_dir(), "error.log"), when="midnight", backupCount=30, encoding="utf-8"
    )
    err_handler.setLevel(logging.WARNING)
    err_handler.setFormatter(fmt)
    err_handler.addFilter(SensitiveFilter())

    logger.addHandler(run_handler)
    logger.addHandler(err_handler)
    return logger
