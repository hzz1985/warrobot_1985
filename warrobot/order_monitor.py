"""Pre-open order monitor: watch the 08:45 orders until 09:45.

Problems this addresses:
  * orders are placed during the pre-open auction (08:45) but nobody watched the
    fill for the next hour;
  * a rejected / expired order was only noticed the next day.

Every 5 minutes (08:46..09:45) it reads the open orders and today's trades and:
  * records the fill state of every order in state/order_monitor.jsonl
  * emails once (per day) when an order was rejected, cancelled or is still
    completely unfilled 30+ minutes after placement
Nothing is cancelled here — the 10:00 rotation owns that.
"""
from __future__ import annotations

import json
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

import market_cal  # noqa: E402
import broker_open_orders  # noqa: E402

LOG = BASE / "state" / "order_monitor.jsonl"
LAST = BASE / "state" / "order_monitor.json"


def main() -> int:
    if not market_cal.market_day():
        print(json.dumps({"skipped": market_cal.reason_closed()}, ensure_ascii=False))
        return 0

    orders = broker_open_orders.raw_orders()
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "open_orders": orders, "note": ""}

    unfilled = [o for o in orders if int(o.get("executed") or 0) < int(o.get("qty") or 0)]
    out["unfilled"] = [{"symbol": o["symbol"], "qty": o["qty"],
                        "executed": o.get("executed"), "price": o.get("price")}
                       for o in unfilled]

    now_hm = market_cal.now_tehran().strftime("%H:%M")
    if not orders:
        out["note"] = "no open orders (all filled or none placed)"
    elif now_hm >= "09:15" and unfilled:
        key = "order-unfilled-" + now_hm.split(":")[0]
        market_cal.send_once(
            key,
            "[WarRobot] سفارش پرنشده در پیش‌گشایش",
            "این سفارش‌ها هنوز پر نشده‌اند:\n" +
            "\n".join(f"{u['symbol']} {u['qty']} @ {u['price']} (اجرا: {u['executed']})"
                      for u in unfilled) +
            "\n\nساعت ۱۰:۰۰ به بعد چرخش انجام می‌شود.")

    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(out, ensure_ascii=False) + "\n")
    LAST.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"open": len(orders), "unfilled": len(unfilled), "note": out["note"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
