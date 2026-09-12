import json
import re
import os

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "novel_page_dump.html")
t = open(path, encoding="utf-8").read()
m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', t, re.DOTALL)
data = json.loads(m.group(1))
props = data["props"]["pageProps"]

ssps = props.get("serverSerializedPreloadedState")
print("serverSerializedPreloadedState type:", type(ssps).__name__, "len:", len(ssps) if isinstance(ssps, str) else "-")
if isinstance(ssps, str):
    state = json.loads(ssps)
    print("state top keys:", list(state.keys()))
    novel = state.get("novel")
    if isinstance(novel, dict):
        print("novel keys:", list(novel.keys())[:20])
        for nid, payload in list(novel.items())[:1]:
            print("novel id:", nid, "payload keys:", list(payload.keys()) if isinstance(payload, dict) else type(payload))
            if isinstance(payload, dict):
                c = payload.get("content")
                print("content present:", c is not None, "len:", len(c) if c else 0)
                print("content preview:", (c or "")[:200])

dh = props.get("dehydratedState")
if isinstance(dh, dict):
    print("dehydratedState keys:", list(dh.keys()))
    for q in dh.get("queries", [])[:5]:
        print("query key:", q.get("queryKey"))
