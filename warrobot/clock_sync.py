"""Re-measure the host clock drift and store it in system.clock_offset_sec.

now_tehran() = OS clock + system.clock_offset_sec. The offset was set once to
+1800 s (2026-10-21, host 30 min slow) and never re-measured by the live stack;
if the host clock gets corrected the bot runs 30 min ahead and the 08:45:01 order
leaves at 08:15 real time. Scheduler job "clock" (07:50) + scheduler start-up +
morning_orders.py all call this. Sources: broker + TSETMC Date headers first.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent / "TeseRobot" / "src"))

if __name__ == "__main__":
    try:
        from tsebot import clock
        res = clock.calibrate(min_change_sec=5.0, force="--force" in sys.argv)
    except Exception as exc:
        res = {"error": str(exc)[:160]}
    print(json.dumps(res, ensure_ascii=False))
    raise SystemExit(0 if "error" not in res else 1)
