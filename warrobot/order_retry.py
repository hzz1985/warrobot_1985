"""If this morning's order job produced no submitted order, try once more.

Runs at 08:52 (while the pre-open window is still open). It looks at today's
orders_log.jsonl:
  * submitted order exists -> nothing to do
  * no submitted order      -> run order_candidates.py again (once) and email
This covers transient failures (captcha, Playwright lock, panel hiccup) without
waiting a whole day.
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
LOG = BASE / "state" / "orders_log.jsonl"
PY = sys.executable
STATE = BASE / "state" / "order_retry.json"


def todays_attempts() -> tuple[int, int]:
    today = datetime.now().strftime("%Y-%m-%d")
    attempts = submitted = 0
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if str(d.get("at") or "")[:10] == today:
                attempts += 1
                submitted += 1 if d.get("submitted") else 0
    return attempts, submitted


def main() -> int:
    attempts, submitted = todays_attempts()
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "attempts": attempts, "submitted": submitted}
    if submitted > 0:
        out["action"] = "none"
        STATE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False))
        return 0
    # one retry inside the pre-open window
    p = subprocess.run([PY, str(BASE / "order_candidates.py")], cwd=str(BASE),
                       capture_output=True, text=True, timeout=1500,
                       encoding="utf-8", errors="replace")
    try:
        res = json.loads((p.stdout or "").strip().splitlines()[-1])
    except Exception:
        res = {"raw": (p.stdout or "")[-300:]}
    out["action"] = "retried"
    out["result_submitted"] = res.get("submitted")
    out["orders"] = res.get("orders")
    STATE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        import tsebot.alerts as alerts
        ok = "✅" if (res.get("submitted") or 0) > 0 else "⚠️"
        alerts.send(f"{ok} تلاش دوبارهٔ سفارش ۰۸:۵۲",
                    f"submitted={res.get('submitted')} orders={res.get('orders')}")
    except Exception:
        pass
    print(json.dumps(out, ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
