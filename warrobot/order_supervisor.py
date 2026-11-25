"""Autonomous order supervisor — retries the pre-open order without any human.

The bot must not depend on a person noticing a failure. Between 08:45 and 09:00
this supervisor is the safety net:

  loop (every 60-90 s, until the window closes):
      * is there a submitted order today?            -> stop (nothing to do)
      * is there already an open order from today?   -> stop (wait for the fill)
      * otherwise: run order_candidates.py again
  at the end of the window: if still nothing, send a RED alert.

It is idempotent: the planner itself refuses to stack a second order, and this
supervisor only acts when neither a submission nor an open order exists.
"""
from __future__ import annotations

import json
import subprocess
import os
import sys
# (Hark fix #2) no forced login: pw/session.js now throttles instead of banning,
# and a forced login per run was what kept kicking the shared session.
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "src"))

PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"
LOG = BASE / "state" / "orders_log.jsonl"
OUT = BASE / "state" / "order_supervisor.json"
PY = sys.executable


def submitted_today() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    n = 0
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if str(d.get("at") or "")[:10] == today and d.get("submitted"):
                n += 1
    return n


def _oc_open() -> list[dict]:
    """Open orders through the resilient reader (profile-reset + retry)."""
    try:
        import order_candidates as oc
        return oc._open_orders()
    except Exception:
        return []


def open_orders() -> list[dict]:
    try:
        p = subprocess.run([NODE, str(PW / "open_orders.js")], cwd=str(PW),
                           capture_output=True, text=True, timeout=420,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("["):
                return json.loads(line)
            if line.startswith("{"):
                return json.loads(line).get("orders", [])
    except Exception:
        pass
    return []


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=240, help="seconds this run may use")
    ap.add_argument("--max-attempts", type=int, default=3)
    args = ap.parse_args()
    attempts = []
    window_end = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    import datetime as _dt
    hard_end = datetime.now().replace(microsecond=0) + _dt.timedelta(seconds=args.budget)
    deadline = min(window_end, hard_end) if datetime.now() < window_end else hard_end
    started = datetime.now()
    result = {"at": started.strftime("%Y-%m-%d %H:%M:%S"), "attempts": []}

    while datetime.now() < deadline:
        if submitted_today() > 0:
            result["stop"] = "order submitted"
            break
        if _oc_open():
            result["stop"] = "open order exists — waiting for the fill"
            break
        p = subprocess.run([PY, str(BASE / "order_candidates.py")], cwd=str(BASE),
                           capture_output=True, text=True, timeout=1500,
                           encoding="utf-8", errors="replace")
        tail = {}
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                try:
                    tail = json.loads(line)
                except Exception:
                    pass
                break
        attempts.append({"at": datetime.now().strftime("%H:%M:%S"),
                         "submitted": tail.get("submitted"),
                         "skipped": tail.get("skipped") or tail.get("note")})
        result["attempts"] = attempts
        if (tail.get("submitted") or 0) > 0:
            result["stop"] = "order submitted"
            break
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        if len(attempts) >= args.max_attempts:
            break
        time.sleep(75)

    if not result.get("stop"):
        result["stop"] = "window closed with no order"
    result["submitted_today"] = submitted_today()
    result["open_orders"] = len(_oc_open())
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    if result["submitted_today"] == 0 and result["open_orders"] == 0:
        try:
            import tsebot.alerts as alerts
            alerts.send("🚨 [WarRobot] هیچ سفارشی تا ۰۹:۰۰ ثبت نشد",
                        json.dumps(result, ensure_ascii=False)[:1200])
        except Exception:
            pass
    print(json.dumps(result, ensure_ascii=False)[:700])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
