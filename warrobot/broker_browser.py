"""The ONE shared, logged-in broker Chrome (Hark fix, bug #2).

Chrome runs once with --remote-debugging-port (config broker.debugger_address,
default 127.0.0.1:9333) on the bot profile state/pw-profile. Every pw/*.js script
attaches to it through pw/cdp_shared.js instead of launching its own Chrome, so
the broker sees ONE session that is logged in once and kept alive
(session_keeper.py), instead of dozens of logins per hour that kicked each other.

    python broker_browser.py            -> ensure Chrome is up, print status
    python broker_browser.py --status   -> status only
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
PROFILE = BASE / "state" / "pw-profile"
CHROME_CANDIDATES = (
    os.environ.get("WARROBOT_CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
)


def shared_enabled() -> bool:
    return os.environ.get("WARROBOT_SHARED_BROWSER", "1") != "0"


def endpoint() -> str:
    ep = os.environ.get("WARROBOT_CDP", "")
    if not ep:
        try:
            cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
            ep = str((cfg.get("broker") or {}).get("debugger_address") or "")
        except Exception:
            ep = ""
    ep = ep or "127.0.0.1:9333"
    return ep if ep.startswith("http") else "http://" + ep


def port() -> int:
    try:
        return int(endpoint().rsplit(":", 1)[1].strip("/"))
    except Exception:
        return 9333


def cdp_alive(timeout: float = 2.5) -> bool:
    try:
        with urllib.request.urlopen(endpoint().rstrip("/") + "/json/version", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def broker_tabs() -> list[dict]:
    try:
        with urllib.request.urlopen(endpoint().rstrip("/") + "/json/list", timeout=3) as r:
            tabs = json.loads(r.read().decode("utf-8", "ignore"))
        return [t for t in tabs if t.get("type") == "page" and "meibours" in str(t.get("url"))]
    except Exception:
        return []


def chrome_path() -> str:
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    return ""


def chrome_args() -> list[str]:
    return [f"--remote-debugging-port={port()}", "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*", f"--user-data-dir={PROFILE}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows", "https://online.meibours.ir/"]


def ensure_browser(wait_sec: float = 25.0) -> dict:
    """Start the shared Chrome if its debugging port does not answer."""
    if cdp_alive():
        return {"ok": True, "started": False, "endpoint": endpoint()}
    # a Chrome WITHOUT the debugging port still holding the profile blocks the start
    try:
        import pw_reset
        pw_reset.kill_orphans(force=True)
    except Exception:
        pass
    exe = chrome_path()
    if not exe:
        return {"ok": False, "started": False, "error": "chrome.exe not found"}
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([exe] + chrome_args(), creationflags=flags, close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        return {"ok": False, "started": False, "error": str(exc)[:120]}
    t0 = time.time()
    while time.time() - t0 < wait_sec:
        if cdp_alive():
            return {"ok": True, "started": True, "endpoint": endpoint()}
        time.sleep(0.5)
    return {"ok": False, "started": True, "error": "debugging port did not open"}


if __name__ == "__main__":
    if "--status" in sys.argv:
        print(json.dumps({"alive": cdp_alive(), "endpoint": endpoint(),
                          "broker_tabs": [t.get("url") for t in broker_tabs()]}, ensure_ascii=False))
    else:
        print(json.dumps(ensure_browser(), ensure_ascii=False))
