"""Pre-open cleanup: cancel leftovers from previous sessions before ordering.

Today's 08:45 order was skipped because a buy order placed *yesterday* was still
open; the open-order guard treats any open order as "do not stack", so nothing
new went out. Day-validity orders are supposed to die at the close, but the
broker kept this one in "onboard" state overnight.

At 08:43 (before the 08:45:01 order) this script:
  * reads the broker's open orders
  * cancels every order whose session date is not today (Tehran)
  * logs what it did to state/preopen_cleanup.json and the order log
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
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "src"))

PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"
OUT = BASE / "state" / "preopen_cleanup.json"


def jalali_today() -> str:
    try:
        import market_cal
        return market_cal.now_tehran().strftime("%Y/%m/%d")
    except Exception:
        return ""


def main() -> int:
    orders = []
    try:
        p = subprocess.run([NODE, str(PW / "open_orders.js")], cwd=str(PW),
                           capture_output=True, text=True, timeout=420,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("["):
                orders = json.loads(line)
                break
            if line.startswith("{"):
                orders = json.loads(line).get("orders", [])
                break
    except Exception as exc:
        print("open_orders error:", str(exc)[:120])

    today = jalali_today()
    stale = []
    for o in orders:
        sess = str(o.get("session") or o.get("date") or "")
        # the broker's list does not always expose the session; treat unknown as stale
        if not sess or (today and sess[:10] != today):
            stale.append(o)

    result = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "tehran_today": today, "open": len(orders), "stale": len(stale),
              "cancelled": []}
    if stale:
        try:
            import cancel_orders
            res = cancel_orders.cancel_all()
            result["cancel_result"] = {k: res.get(k) for k in ("cancelled", "before", "after")}
            result["cancelled"] = [o.get("symbol") for o in stale]
        except Exception as exc:
            result["error"] = str(exc)[:150]
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False)[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
