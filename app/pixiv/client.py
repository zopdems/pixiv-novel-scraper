import random
import re
import time

from curl_cffi import requests as curl_requests

from ..config import Settings


class AuthExpiredError(Exception):
    pass


class RiskControlError(Exception):
    pass


class PixivRequestError(Exception):
    def __init__(self, message: str, kind: str = "network"):
        super().__init__(message)
        self.kind = kind


_PHPSESSID_RE = re.compile(r"PHPSESSID=([^;\s]+)")


def extract_phpsessid(cookie_str: str) -> str:
    m = _PHPSESSID_RE.search(cookie_str or "")
    return m.group(1) if m else ""


def _is_cloudflare_challenge(resp) -> bool:
    if resp.status_code != 403:
        return False
    head = resp.text[:2000]
    return "Just a moment" in head or "cf-chl" in head


class PixivClient:
    BASE = "https://www.pixiv.net"

    def __init__(self, settings: Settings, cookie: str, logger=None, stop_event=None):
        self.settings = settings
        self.logger = logger
        self._last_request_at = 0.0
        self._consecutive_failures = 0
        self._stop_event = stop_event
        headers = {
            "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8",
            "Referer": self.BASE + "/",
        }
        if settings.user_agent:
            headers["User-Agent"] = settings.user_agent
        proxies = self._build_proxies(settings)
        self._http = curl_requests.Session(
            impersonate="chrome",
            headers=headers,
            proxies=proxies,
            timeout=30.0,
            allow_redirects=True,
        )
        sessid = extract_phpsessid(cookie)
        if sessid:
            self._http.cookies.set("PHPSESSID", sessid, domain=".pixiv.net")

    @staticmethod
    def _build_proxies(settings: Settings):
        p = settings.proxy
        if p.enabled and p.host:
            auth = f"{p.username}:{p.password}@" if p.username else ""
            scheme = "socks5" if p.scheme.startswith("socks") else p.scheme
            url = f"{scheme}://{auth}{p.host}:{p.port}"
            return {"http": url, "https": url}
        return PixivClient._system_proxy()

    @staticmethod
    def _system_proxy():
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            if not enabled or not server:
                return None
            if "=" in server:
                parts = dict(
                    item.split("=", 1) for item in server.split(";") if "=" in item
                )
                server = parts.get("http") or parts.get("https") or ""
            server = server.strip()
            if not server:
                return None
            url = server if "://" in server else f"http://{server}"
            return {"http": url, "https": url}
        except Exception:
            return None

    def close(self):
        self._http.close()

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def _sleep(self, seconds: float):
        if self._stop_event is not None:
            self._stop_event.wait(seconds)
        else:
            time.sleep(seconds)

    def _throttle(self):
        interval = max(0.5, float(self.settings.request_interval))
        # 每次请求间隔叠加 ±20% 随机抖动：固定节奏更容易被风控识别为机器人，
        # 抖动能打散请求的机械规律，同时对总速率影响很小。
        jitter = interval * random.uniform(-0.2, 0.2)
        effective = max(0.3, interval + jitter)
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < effective:
            self._sleep(effective - elapsed)
        self._last_request_at = time.monotonic()

    def request(self, method: str, url: str, **kwargs):
        backoff = [1.0, 2.0, 4.0]
        last_exc = None
        for attempt in range(4):
            self._throttle()
            try:
                resp = self._http.request(method, url, **kwargs)
            except Exception as e:
                last_exc = e
                if attempt < 3:
                    self._sleep(backoff[attempt])
                continue
            if _is_cloudflare_challenge(resp):
                self._consecutive_failures += 1
                raise RiskControlError("被 Cloudflare 风控拦截（403），请稍后重试或配置代理")
            if resp.status_code == 401:
                self._consecutive_failures += 1
                raise AuthExpiredError("登录已失效（401）")
            if resp.status_code == 403:
                self._consecutive_failures += 1
                raise RiskControlError("请求被拒绝（403，UA/风控/权限不足）")
            if resp.status_code == 404:
                raise PixivRequestError("资源不存在（404）", kind="not_found")
            if resp.status_code >= 500:
                last_exc = PixivRequestError(f"服务器错误（{resp.status_code}）", kind="network")
                if attempt < 3:
                    self._sleep(backoff[attempt])
                continue
            if resp.status_code >= 400:
                raise PixivRequestError(f"请求失败（{resp.status_code}）", kind="api")
            self._consecutive_failures = 0
            return resp
        self._consecutive_failures += 1
        if isinstance(last_exc, PixivRequestError):
            raise last_exc
        raise PixivRequestError(f"网络请求失败：{last_exc}", kind="network")

    def get_json(self, url: str, **kwargs) -> dict:
        resp = self.request("GET", url, **kwargs)
        try:
            data = resp.json()
        except ValueError:
            if "login" in str(resp.url) or "ログイン" in resp.text[:2000]:
                raise AuthExpiredError("登录已失效（被重定向到登录页）")
            raise PixivRequestError("响应不是有效 JSON", kind="parse")
        if isinstance(data, dict) and data.get("error") is True:
            message = str(data.get("message") or "")
            if "ログイン" in message or "login" in message.lower():
                raise AuthExpiredError("登录已失效（接口要求登录）")
            raise PixivRequestError(f"接口返回错误：{message[:200]}", kind="api")
        return data

    def get_text(self, url: str, **kwargs) -> str:
        resp = self.request("GET", url, **kwargs)
        if "login" in str(resp.url):
            raise AuthExpiredError("登录已失效（被重定向到登录页）")
        return resp.text

    def download_binary(self, url: str, referer: str = None) -> bytes:
        headers = {"Referer": referer or self.BASE + "/"}
        resp = self.request("GET", url, headers=headers)
        return resp.content

    def test_connection(self) -> tuple:
        try:
            self._throttle()
            resp = self._http.get(self.BASE + "/ajax/settings?lang=zh")
        except Exception as e:
            return False, f"连接失败：{e}"
        if _is_cloudflare_challenge(resp):
            return False, "被 Cloudflare 风控拦截，请稍后重试或配置代理"
        try:
            data = resp.json()
        except ValueError:
            return False, f"响应异常（HTTP {resp.status_code}）"
        if isinstance(data, dict) and data.get("error") is False:
            return True, "连接成功，登录有效"
        return False, "Cookie 无效或已过期，请重新导入"
