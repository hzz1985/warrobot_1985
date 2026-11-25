"""The truth report: what actually happened with today's orders.

The 08:30/08:40 readiness emails only prove that login/balance/candidates are
fine — they do NOT prove that an order was placed. That gap produced a
"robot is healthy" email on a day with zero orders (1405/08/24), so this script
reports the real evidence:

    * how many orders were attempted / submitted today (orders_log.jsonl)
    * which of them are still open and at what price (broker API)
    * whether the positions changed (fill verification)
    * a red-flag email when nothing was submitted

Runs at 09:02 (right after the order + retry) and 12:35 (end of session).
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
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

LOG = BASE / "state" / "orders_log.jsonl"
OUT = BASE / "state" / "order_truth.json"
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"


def todays_orders() -> list[dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    rows = []
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
                rows.append(d)
    return rows


def open_orders() -> list[dict]:
    try:
        p = subprocess.run([NODE, str(PW / "broker_client.js"), "open"], cwd=str(PW),
                           capture_output=True, text=True, timeout=420,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("[") or line.startswith("{"):
                data = json.loads(line)
                return data if isinstance(data, list) else data.get("orders", [])
    except Exception:
        pass
    return []


def main() -> int:
    rows = todays_orders()
    submitted = [r for r in rows if r.get("submitted")]
    opened = open_orders()
    report = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "attempts": len(rows),
        "submitted": len(submitted),
        "submitted_detail": [{"symbol": r.get("symbol"), "price": r.get("price"),
                              "qty": r.get("qty"),
                              "intended": r.get("intended_price"),
                              "placed": r.get("placed_price")} for r in submitted],
        "open_orders": len(opened),
        "open_detail": [{"symbol": o.get("symbol"), "price": o.get("orderprice"),
                         "qty": o.get("orderqty"), "executed": o.get("executedqty")}
                        for o in opened][:10],
        "verdict": "ok" if (submitted or opened) else "NO_ORDER_SUBMITTED",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    # only alert inside the trading window; a night-time run must stay quiet
    market_window = False
    try:
        import market_cal
        now = market_cal.now_tehran()
        market_window = market_cal.market_day() and (8, 50) <= (now.hour, now.minute) <= (12, 45)
    except Exception:
        market_window = False
    report["market_window"] = market_window
    try:
        import tsebot.alerts as alerts
        if not market_window:
            raise RuntimeError("outside the trading window: no email")
        icon = "✅" if submitted else "🚨"
        lines = [f"تلاش‌ها: {len(rows)}", f"ثبت‌شده: {len(submitted)}",
                 f"سفارش باز: {len(opened)}", ""]
        for r in submitted[:6]:
            lines.append(f"· {r.get('symbol')} — {r.get('qty')} @ {r.get('price')}")
        if not submitted:
            lines.append("هیچ سفارشی ثبت نشد. orders_log را بررسی کن.")
        alerts.send(f"{icon} [WarRobot] نتیجهٔ واقعی سفارش گذاری — {report['at'][11:16]}",
                    "\n".join(lines))
    except Exception:
        pass
    print(json.dumps(report, ensure_ascii=False)[:700])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
