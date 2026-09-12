import time


class DailyScheduler:
    def __init__(self, fetch_time: str = "03:00"):
        self.fetch_time = fetch_time
        self._last_run_date = ""

    def set_time(self, fetch_time: str):
        self.fetch_time = fetch_time

    def should_run(self, now=None) -> bool:
        now = now or time.localtime()
        today = time.strftime("%Y-%m-%d", now)
        if self._last_run_date == today:
            return False
        try:
            hh, mm = (int(x) for x in self.fetch_time.split(":"))
        except ValueError:
            return False
        if (now.tm_hour, now.tm_min) >= (hh, mm):
            self._last_run_date = today
            return True
        return False

    def mark_run_today(self):
        self._last_run_date = time.strftime("%Y-%m-%d")
