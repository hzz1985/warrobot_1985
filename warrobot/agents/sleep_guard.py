"""A6 — sleep / pm2 / service guard.

Owns the "Windows slept or pm2 died and nothing ran" class of failure. Runs every
few minutes (all day), and if a service is missing it brings it back, then proves
it with a real HTTP/API check.

    python -m agents.sleep_guard
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

NODE = r"C:\Program Files\nodejs\node.exe"
PM2_BIN = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "pm2" / "bin" / "pm2"


def _pm2_raw(*args):
    """pm2 CLI through node — no cmd.exe window."""
    return subprocess.run([NODE, str(PM2_BIN), *args], capture_output=True, text=True,
                          timeout=120, encoding="utf-8", errors="replace")


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
OUT = STATE / "agents" / "a6_services.json"
BOT = BASE                      # WarRobot
TESE = BASE.parent / "TeseRobot"

# name -> (cwd, script)
EXPECTED = {
    "warrobot": (BOT, "daemon_service.py"),
    "warsched": (BOT, "scheduler.py"),
    "tse-web": (TESE, r"scripts\web_supervisor.py"),
    "tse-proxy": (TESE, r"scripts\proxy80.js"),
    "tse-v6": (TESE, r"scripts\v6bridge.js"),
}
CHECK_URLS = ["http://127.0.0.1:5077/", "http://127.0.0.1:8090/"]


def _pm2() -> dict:
    try:
        p = _pm2_raw("jlist")
        data = json.loads(p.stdout or "[]")
        return {x.get("name"): x.get("pm2_env", {}).get("status") for x in data}
    except Exception:
        return {}


def _start(name: str) -> dict:
    cwd, script = EXPECTED[name]
    try:
        inner = (f'pm2 start {script}' if script.endswith(".js")
                 else f'pm2 start python --name {name} -- -u {script}')
        p = subprocess.run(["cmd", "/c", inner], cwd=str(cwd), capture_output=True,
                           text=True, timeout=300, encoding="utf-8", errors="replace")
        return {"started": p.returncode == 0, "tail": (p.stdout or p.stderr or "")[-160:]}
    except Exception as exc:
        return {"started": False, "error": str(exc)[:120]}


def _http() -> dict:
    res = {}
    for u in CHECK_URLS:
        try:
            with urllib.request.urlopen(u, timeout=15) as r:
                res[u] = r.status
        except Exception as exc:
            res[u] = f"ERR {str(exc)[:40]}"
    return res


def run(repair: bool = True) -> dict:
    out = {"agent": "a6_services", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "before": _pm2(), "actions": []}
    missing = [n for n, st in EXPECTED.items()
               if out["before"].get(n) not in ("online", "launching")]
    out["missing"] = missing

    if missing and repair:
        for name in missing:
            out["actions"].append({name: _start(name)})
            time.sleep(2)
        try:
            _pm2_raw("save")
        except Exception:
            pass

    out["after"] = _pm2()
    out["http"] = _http()
    out["ok"] = (all(out["after"].get(n) in ("online", "launching") for n in EXPECTED)
                 and all(v == 200 for v in out["http"].values()))
    if not out["ok"]:
        out["escalate"] = f"services not healthy: {out['after']} / {out['http']}"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(repair="--no-repair" not in sys.argv), ensure_ascii=False)[:700])
