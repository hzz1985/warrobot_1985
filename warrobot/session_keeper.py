"""Keep ONE broker session alive (Hark fix, bug #2).

    python session_keeper.py --once      one check (scheduler calls it every minute)
    python session_keeper.py --loop 45   own loop (optional pm2 app "warsession")
    python session_keeper.py --no-login  check only, never log in

Each check:
  1. ensure the shared Chrome (broker_browser.ensure_browser) is up;
  2. run pw/keepalive.js: an in-page API call keeps the session warm and proves it
     is authenticated; only when the broker really logged us out does it log in
     again (OCR captcha), throttled by pw/session.js (>= 90 s apart, <= 30/day,
     paused while state/OPERATOR_HOLD exists);
  3. write state/session_health.json and append state/session_health.jsonl.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
PW = BASE / "pw"
NODE = os.environ.get("WARROBOT_NODE", r"C:\Program Files\nodejs\node.exe")
HEALTH = BASE / "state" / "session_health.json"
HISTORY = BASE / "state" / "session_health.jsonl"


def _node() -> str:
    return NODE if Path(NODE).exists() else "node"


def check(allow_login: bool = True, priority: bool = False, timeout: int = 300) -> dict:
    import broker_browser
    rec = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    br = broker_browser.ensure_browser()
    rec["browser"] = br
    if not br.get("ok"):
        rec.update(ok=False, state="browser_down")
        _write(rec)
        return rec
    env = dict(os.environ)
    if priority:
        env["WARROBOT_UI_PRIORITY"] = "1"
    args = [_node(), str(PW / "keepalive.js")] + ([] if allow_login else ["--no-login"])
    try:
        p = subprocess.run(args, cwd=str(PW), capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", env=env)
        data = {}
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                data = json.loads(line)
                break
        rec.update(data or {"ok": False, "state": "no_output",
                            "stderr": (p.stderr or "")[-200:]})
    except Exception as exc:
        rec.update(ok=False, state="error", error=str(exc)[:160])
    _write(rec)
    return rec


def _write(rec: dict) -> None:
    try:
        HEALTH.parent.mkdir(parents=True, exist_ok=True)
        HEALTH.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        with HISTORY.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main() -> int:
    allow = "--no-login" not in sys.argv
    if "--loop" in sys.argv:
        try:
            every = float(sys.argv[sys.argv.index("--loop") + 1])
        except Exception:
            every = 45.0
        while True:
            r = check(allow_login=allow)
            print(json.dumps({k: r.get(k) for k in ("at", "ok", "state")}, ensure_ascii=False), flush=True)
            time.sleep(max(15.0, every))
    r = check(allow_login=allow, priority="--priority" in sys.argv)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
