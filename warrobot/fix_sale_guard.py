"""Price-band guard for sells + lessons for the missing-sale incident."""
from pathlib import Path

# ---------- 1) lot_exits: never send an absurd price ----------
p = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\lot_exits.py")
s = p.read_text(encoding="utf-8")
if "price_out_of_band" not in s:
    s = s.replace('''        price = int(float(prices_cache.get(sym) or 0))
        if qty <= 0 or price <= 0:
            continue''',
'''        price = int(float(prices_cache.get(sym) or 0))
        if qty <= 0 or price <= 0:
            continue
        # reject a price that cannot be real (stale snapshot / parse error)
        try:
            import json as _j
            _pnl = _j.loads((BASE / "state" / "pnl.json").read_text(encoding="utf-8"))
            _avg = next((float(x.get("avg_cost") or 0) for x in _pnl.get("positions", [])
                         if x.get("symbol") == sym), 0.0)
        except Exception:
            _avg = 0.0
        if _avg and not (0.5 * _avg <= price <= 2.0 * _avg):
            placed.append({"symbol": sym, "qty": qty, "price": price,
                           "reason": "price_out_of_band", "avg": _avg,
                           "submitted": False})
            continue''')
    p.write_text(s, encoding="utf-8")
    print("lot_exits: price band guard")

# ---------- 2) intraday_exit: same guard ----------
ie = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\intraday_exit.py")
t = ie.read_text(encoding="utf-8")
if "exit_price_out_of_band" not in t:
    t = t.replace('''            px = float(row["price"])
            qty = int(row["qty"])''',
'''            px = float(row["price"])
            qty = int(row["qty"])
            avg = float(row.get("avg_cost") or 0)
            if avg and not (0.5 * avg <= px <= 2.0 * avg):
                out["positions"][-1]["note"] = "exit_price_out_of_band"
                continue''')
    ie.write_text(t, encoding="utf-8")
    print("intraday_exit: price band guard")

# ---------- 3) lessons: teach the agents ----------
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
from agents import lessons  # noqa: E402

lessons.record(
    "فروش انجام شده ولی در ربات ثبت نشده",
    "hook ثبت فروش فقط با متن پیام موفق اجرا می‌شد؛ پاسخ خطا/خالی → بدون realized و ژورنال",
    "agents/sale_reconcile.py: تطبیق شبانهٔ معاملات کارگزار با دفترها و ثبت خودکار فروش جاافتاده",
    "state/agents/a10_sales.json → missing=[]",
    ["sale", "record", "reconcile", "فروش"],
    source="architect",
)
lessons.record(
    "سفارش فروش با قیمت غیرواقعی ۴۳۷۰ رد شد",
    "قیمت از اسنپ‌شات کهنه/خراب pnl گرفته شد و بدون اعتبارسنجی ارسال شد",
    "گارد باند قیمت در lot_exits و intraday_exit: ۰.۵×میانگین ≤ قیمت ≤ ۲×میانگین",
    "exits_log → reason=price_out_of_band (بدون ارسال سفارش)",
    ["sell", "price", "band", "قیمت"],
    source="architect",
)
print("lessons recorded")

# ---------- 4) scheduler + supervisor wiring ----------
sc = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\scheduler.py")
x = sc.read_text(encoding="utf-8")
if "sale_reconcile" not in x:
    x = x.replace('''    ("21:14", "trades", ["trades_ledger.py"]),''',
'''    ("21:12", "salesrec", ["agents/sale_reconcile.py"]),
    ("21:14", "trades", ["trades_ledger.py"]),''')
    x = x.replace('"sellrev", "weekly", "calendar", "calsync"}',
                  '"sellrev", "weekly", "calendar", "calsync", "salesrec"}')
    sc.write_text(x, encoding="utf-8")
    print("scheduler: 21:12 sale reconciliation")

sup = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\agents\supervisor.py")
u = sup.read_text(encoding="utf-8")
if '"a10"' not in u:
    u = u.replace('"a3": "webapp_watch", "a4": "log_triage"}',
                  '"a3": "webapp_watch", "a4": "log_triage",\n          "a10": "sale_reconcile"}')
    sup.write_text(u, encoding="utf-8")
    print("supervisor: a10 registered")
