"""Persist the pm2 process list every night (and report what is running).

Without a fresh dump, a Windows restart restores an OLD pm2 list — that is
exactly how warrobot and warsched disappeared on 1405-08-20.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
OUT = BASE / "state" / "pm2_save.json"
NAMES = ["warrobot", "warsched", "tse-web"]


def status() -> dict:
    try:
        p = subprocess.run(["pm2", "list", "--no-color"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=90, shell=True)
        out = p.stdout or ""
    except Exception as exc:
        return {"error": str(exc)[:120]}
    rows = {}
    for name in NAMES:
        # a row looks like: │ 3 │ warrobot │ default │ ... │ online │
        for line in out.splitlines():
            if f" {name} " in line and "online" in line:
                rows[name] = "online"
                break
            if f" {name} " in line and ("errored" in line or "stopped" in line):
                rows[name] = "down"
                break
        rows.setdefault(name, "missing")
    return rows


def main() -> int:
    st = status()
    try:
        subprocess.run(["pm2", "save"], capture_output=True, text=True, timeout=120, shell=True)
        saved = True
    except Exception:
        saved = False
    result = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "services": st, "saved": saved,
              "all_online": all(v == "online" for v in st.values())}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["all_online"] and saved else 2


if __name__ == "__main__":
    raise SystemExit(main())
