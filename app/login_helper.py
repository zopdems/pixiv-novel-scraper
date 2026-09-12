import sys
import threading
import time

from .credentials import save_cookie

LOGIN_URL = "https://accounts.pixiv.net/login?lang=zh"


def run() -> int:
    try:
        import webview
    except ImportError:
        print("缺少 pywebview 依赖", file=sys.stderr)
        return 2

    result = {"cookie": ""}
    window = webview.create_window(
        "登录 Pixiv（登录成功后窗口自动关闭）", LOGIN_URL, width=480, height=760
    )

    def poll():
        while True:
            time.sleep(2)
            try:
                cookies = window.get_cookies() or []
                for c in cookies:
                    if getattr(c, "name", "") == "PHPSESSID" and c.value:
                        result["cookie"] = f"PHPSESSID={c.value}"
                        save_cookie(result["cookie"])
                        window.destroy()
                        return
            except Exception:
                return

    threading.Thread(target=poll, daemon=True).start()
    webview.start()
    return 0 if result["cookie"] else 1


if __name__ == "__main__":
    sys.exit(run())
