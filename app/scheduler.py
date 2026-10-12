import time


class DailyScheduler:
    def __init__(self, fetch_time: str = "03:00", catch_up_window_minutes: int = 0):
        self.fetch_time = fetch_time
        # 0 = 当天任意时间启动都补跑（历史行为）；>0 表示只在该时间窗口内补跑
        self.catch_up_window_minutes = int(catch_up_window_minutes or 0)
        self._last_run_date = ""

    def set_time(self, fetch_time: str):
        self.fetch_time = fetch_time

    def set_catch_up_window(self, minutes: int):
        self.catch_up_window_minutes = int(minutes or 0)

    def should_run(self, now=None) -> bool:
        now = now or time.localtime()
        today = time.strftime("%Y-%m-%d", now)
        if self._last_run_date == today:
            return False
        try:
            hh, mm = (int(x) for x in self.fetch_time.split(":"))
        except ValueError:
            return False
        if (now.tm_hour, now.tm_min) < (hh, mm):
            return False
        if self.catch_up_window_minutes > 0:
            elapsed = (now.tm_hour * 60 + now.tm_min) - (hh * 60 + mm)
            if elapsed > self.catch_up_window_minutes:
                # 已错过补跑窗口，本日不再触发
                return False
        self._last_run_date = today
        return True

    def mark_run_today(self):
        self._last_run_date = time.strftime("%Y-%m-%d")
