import json
import re
import os

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "novel_page_dump.html")
t = open(path, encoding="utf-8").read()

m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', t, re.DOTALL)
print("found:", bool(m))
if m:
    data = json.loads(m.group(1))
    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("content", "text", "novel"):
                    print(f"{path}.{k}: type={type(v).__name__}, preview={str(v)[:120]!r}")
                walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj[:3]):
                walk(v, f"{path}[{i}]")
    walk(data)
    print("top keys:", list(data.keys()))
    props = data.get("props", {}).get("pageProps", {})
    print("pageProps keys:", list(props.keys()))
