"""Load the dashboard in the REAL desktop Chrome (CDP 9222) and report errors.

Evidence gatherer for "the web app does not open in desktop Chrome":
it opens a tab, navigates, records console errors / failed requests, and saves a
screenshot so we can see what the browser actually renders.
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

import websocket

OUT = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\state\desktop_chrome_probe")
OUT.mkdir(parents=True, exist_ok=True)


def cdp_targets() -> list[dict]:
    return json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=10).read())


def new_tab(url: str) -> dict:
    # modern Chrome wants PUT for /json/new
    req = urllib.request.Request(f"http://127.0.0.1:9222/json/new?{url}", method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def probe(url: str) -> dict:
    tab = new_tab(url)
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
    mid = [0]
    events = []

    def send(method: str, params: dict | None = None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                msg = json.loads(ws.recv())
            except Exception:
                break
            if msg.get("id") == mid[0]:
                return msg
            if "method" in msg:
                events.append(msg)
        return {}

    send("Page.enable")
    send("Runtime.enable")
    send("Log.enable")
    send("Network.enable")
    send("Page.navigate", {"url": url})
    time.sleep(6)
    send("Runtime.evaluate", {"expression": "1"})   # flush events

    errors, failed = [], []
    for ev in events:
        m = ev.get("method")
        p = ev.get("params") or {}
        if m == "Runtime.exceptionThrown":
            errors.append(str(p.get("exceptionDetails", {}).get("text"))[:160])
        elif m == "Log.entryAdded":
            e = p.get("entry") or {}
            if e.get("level") in ("error", "warning"):
                errors.append(f"{e.get('level')}: {str(e.get('text'))[:140]}")
        elif m == "Network.loadingFailed":
            failed.append(f"{p.get('errorText')} ({p.get('type')})")
        elif m == "Runtime.consoleAPICalled":
            args = [a.get("value") or a.get("description") for a in p.get("args") or []]
            txt = " ".join(str(a) for a in args if a)
            if txt:
                errors.append("console: " + txt[:140])

    shot = send("Page.captureScreenshot", {"format": "png"})
    data = (shot.get("result") or {}).get("data")
    path = OUT / f"desktop_{url.replace('://', '_').replace('/', '_').replace(':', '_')}.png"
    if data:
        path.write_bytes(base64.b64decode(data))
    info = send("Runtime.evaluate", {"expression": "document.title + '|' + location.href + '|' + document.body.innerText.slice(0,120)", "returnByValue": True})
    text = ((info.get("result") or {}).get("result") or {}).get("value")
    ws.close()
    return {"url": url, "tab": tab.get("url"), "page": text,
            "errors": errors[:12], "failed": failed[:12], "screenshot": str(path)}


if __name__ == "__main__":
    urls = sys.argv[1:] or ["http://localhost/", "http://127.0.0.1:5077/"]
    results = [probe(u) for u in urls]
    print(json.dumps(results, ensure_ascii=False, indent=1)[:3000])
