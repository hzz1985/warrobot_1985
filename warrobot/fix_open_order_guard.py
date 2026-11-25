"""Stop order_candidates from stacking a second order on top of an open one.

The morning test showed the planner wanted to buy آباد while a 527M buy order for
آواپارس was still open (its cash blocked). That is double spending: the planner
only looked at AccountBalance and the holdings, never at open orders.

Rule now enforced before planning:
  * read the broker's open orders
  * if any buy/sell order is already open -> do not place another one
    (the 10:00 rotation cancels/replaces unfilled orders explicitly)
  * and record the reason in the log so the night report can show it
"""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\order_candidates.py")
s = P.read_text(encoding="utf-8")

if "_open_orders" not in s:
    s = s.replace('''def _broker_prices() -> dict:''',
'''def _open_orders() -> list[dict]:
    """Open orders straight from the broker (prevents double spending)."""
    try:
        p = subprocess.run([NODE, str(PW / "open_orders.js")], cwd=str(PW),
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


def _broker_prices() -> dict:''')

    s = s.replace('''    out["free_balance"] = round(free, 0)''',
'''    out["free_balance"] = round(free, 0)
    open_orders = _open_orders()
    out["open_orders"] = [{"symbol": o.get("symbol"), "side": o.get("side"),
                           "qty": o.get("qty"), "executed": o.get("executed"),
                           "price": o.get("price")} for o in open_orders]
    if open_orders:
        out["skipped"] = "open_order_pending"
        out["note"] = ("یک سفارش باز وجود دارد؛ سهم دیگری ثبت نمی‌شود "
                       "(چرخش ۱۰:۰۰ ناظر لغو/جایگزینی است)")
        out["orders"] = []
        out["submitted"] = 0
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                 "symbol": None, "source": "open_order_guard",
                                 "submitted": False,
                                 "open_orders": len(open_orders)}) + "\\n")
        return out''')
    P.write_text(s, encoding="utf-8")
    print("open-order guard added")
else:
    print("guard already present")
