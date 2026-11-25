"""Dry run of exit_engine decisions incl. the 15-session time exit. Sends nothing."""
import json, os, sys
from datetime import date, timedelta
from pathlib import Path
os.environ["FLOW_DRY"] = "1"
BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE)); sys.path.insert(0, str(BASE / "src"))
os.chdir(BASE)
import exit_engine as ee

def boom(*a, **k):
    raise RuntimeError("order path called in dry test")
ee.place_sell = boom

cfg = ee.config()
pnl = json.loads((BASE / "state" / "pnl.json").read_text(encoding="utf-8"))
positions = pnl.get("positions") or []
prices = {p["symbol"]: float(p.get("price") or 0) for p in positions}
lots = ee.open_lots()
hol = ee.holiday_set()
today = ee.tehran_today()
print("cfg:", json.dumps(cfg))
print("tehran_today:", today, "holidays loaded:", None if hol is None else len(hol))
print("pnl.json at:", pnl.get("at"), "(prices from that snapshot)")
for sym, ls in lots.items():
    for l in ls:
        ent = ee.entry_session(l["at"])
        print(f"lot {sym}: at(server)={l['at']} -> entry session (Tehran) {ent}, "
              f"sessions held as of {today}: {ee.sessions_after(ent, today, hol)}")
        d, n = ent, 0
        while n < cfg["hold"]:
            d += timedelta(days=1)
            if ee.is_session(d, hol):
                n += 1
        print(f"   -> time exit due on session {d} ({ee._jalali(d)}) unless a new holiday is added")

def show(day):
    decs = ee.plan(positions, lots, prices, {}, [], cfg, day.strftime("%Y-%m-%d"),
                   recent_qty=lambda s: 0, today_tehran=day, holidays=hol)
    print(f"== decisions as of {day} ==")
    for d in decs:
        print(json.dumps({"symbol": d.symbol, "action": d.action, "reason": d.reason,
                          "due_qty": d.due_qty, "qty": d.qty, "price": d.order_price,
                          "pnl_pct": d.pnl_pct, "note": d.note,
                          "lots": [{"at": l["at"], "verdict": l["verdict"], "pct": l["pct"],
                                    "sessions_held": l.get("sessions_held")} for l in d.lots]},
                         ensure_ascii=False))
show(today)
show(date(2026, 11, 28))
for sym, ls in lots.items():
    ent = ee.entry_session(ls[0]["at"]); d, n = ent, 0
    while n < cfg["hold"]:
        d += timedelta(days=1); n += ee.is_session(d, hol)
    show(d - timedelta(days=1) if ee.is_session(d - timedelta(days=1), hol) else d - timedelta(days=3))
    show(d)
    break
