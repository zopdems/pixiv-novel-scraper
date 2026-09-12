import json
import re
import os

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "novel_page_dump.html")
t = open(path, encoding="utf-8").read()
m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', t, re.DOTALL)
data = json.loads(m.group(1))
state = json.loads(data["props"]["pageProps"]["serverSerializedPreloadedState"])

page = state.get("page")
print("page type:", type(page).__name__)
if isinstance(page, dict):
    print("page keys:", list(page.keys()))
    novel = page.get("novel")
    if isinstance(novel, dict):
        print("novel keys:", list(novel.keys()))
        tags = novel.get("tags")
        print("tags:", str(tags)[:200])
        print("seriesNavData:", str(novel.get("seriesNavData"))[:200])
        print("content len:", len(novel.get("content") or ""))
        print("title:", novel.get("title"), "| user:", novel.get("userName"))

print()
print("'seriesNavData' in raw state:", '"seriesNavData"' in json.dumps(state)[:200000])
print("'tags' in page json:", '"tags"' in json.dumps(page))
