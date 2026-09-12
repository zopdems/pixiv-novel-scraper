import os
import re
import sys

from curl_cffi import requests as cr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Settings
from app.pixiv.client import PixivClient

s = Settings()
client = PixivClient(s, "")
proxies = PixivClient._build_proxies(s)
print("proxies:", proxies)

resp = client._http.get("https://www.pixiv.net/novel/show.php?id=18418657&lang=zh")
print("status:", resp.status_code)
print("url:", resp.url)
t = resp.text
print("len:", len(t))
for marker in ["meta-preload-data", "meta-global-data", "__NEXT_DATA__", "novel", "content", "ログイン"]:
    print(f"{marker!r} in page:", marker in t)

m = re.search(r"""<script[^>]*id=["']meta-preload-data["'][^>]*>""", t)
print("preload tag:", (m.group(0)[:300] if m else None))
m2 = re.search(r"""<script[^>]*id=["']meta-global-data["'][^>]*>""", t)
print("global tag:", (m2.group(0)[:300] if m2 else None))

dump = os.path.join(os.path.dirname(os.path.abspath(__file__)), "novel_page_dump.html")
with open(dump, "w", encoding="utf-8") as f:
    f.write(t)
print("dumped to", dump)
