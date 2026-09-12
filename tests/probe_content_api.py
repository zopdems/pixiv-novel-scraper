import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import Settings
from app.pixiv.client import PixivClient

s = Settings()
client = PixivClient(s, "")
nid = "18418657"

for url in [
    f"https://www.pixiv.net/ajax/novel/{nid}?lang=zh",
    f"https://www.pixiv.net/ajax/novel/{nid}/content?lang=zh",
    f"https://www.pixiv.net/ajax/novel/{nid}/text?lang=zh",
]:
    try:
        resp = client._http.get(url)
        print(url.split("pixiv.net")[-1], "->", resp.status_code, "len", len(resp.text))
        if resp.status_code == 200:
            try:
                data = resp.json()
                body = data.get("body")
                if isinstance(body, dict):
                    print("  body keys:", list(body.keys())[:25])
                    if "content" in body:
                        c = body["content"]
                        print("  content len:", len(c), "preview:", c[:150])
                else:
                    print("  body type:", type(body).__name__, str(body)[:120])
            except ValueError:
                print("  not json:", resp.text[:120])
    except Exception as e:
        print(url.split("pixiv.net")[-1], "-> EXC", str(e)[:120])
client.close()
