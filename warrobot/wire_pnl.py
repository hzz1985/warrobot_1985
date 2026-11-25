"""Wire the corrected P/L into the dashboard and the exit watcher.

1) warrobot_data.positions() now reads state/pnl.json (panel prices + real cost
   basis + the correct formula) and falls back to the old path only if missing
2) intraday_exit.py watches the same numbers (panel price, same avg cost), so a
   take-profit/stop-loss triggers on the values the operator actually sees
3) scheduler refreshes state/pnl.json every 3 minutes during the session
"""
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
WEB = WAR.parent / "TeseRobot" / "webapp"

# ------------------------------------------------------- 1) dashboard source
p = WEB / "warrobot_data.py"
s = p.read_text(encoding="utf-8")
if "pnl.json" not in s:
    s = s.replace('''def positions() -> list[dict]:''',
'''def _pnl_snapshot() -> list[dict]:
    """Positions with the corrected P/L (state/pnl.json built from panel prices)."""
    fp = STATE / "pnl.json"
    if not fp.exists():
        return []
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = []
    for p in d.get("positions", []):
        rows.append({
            "symbol": p["symbol"],
            "qty": int(p["qty"]),
            "avg": float(p.get("avg_cost") or 0),
            "price": float(p.get("price") or 0),
            "value": float(p.get("value") or 0),
            "invested": float(p.get("invested") or 0),
            "pnl_pct": float(p.get("pnl_pct") or 0),
            "total_pct": float(p.get("pnl_pct") or 0),
            "total_pnl": float(p.get("pnl_rial") or 0),
            "avg_source": p.get("avg_source"),
            "status": p.get("status"),
            "to_tp_pp": p.get("to_tp_pp"),
            "to_sl_pp": p.get("to_sl_pp"),
        })
    return rows


def positions() -> list[dict]:''')
    s = s.replace('''    prices = _price_map()
    broker = _broker_positions()''',
'''    snap = _pnl_snapshot()
    if snap:
        return snap
    prices = _price_map()
    broker = _broker_positions()''')
    p.write_text(s, encoding="utf-8")
    print("dashboard now reads state/pnl.json")
else:
    print("dashboard already wired")

# ------------------------------------------------- 2) exit watcher uses pnl
ie = WAR / "intraday_exit.py"
e = ie.read_text(encoding="utf-8")
if "pnl_rows" not in e:
    e = e.replace('''    for pos in positions():
        sym = pos["symbol"]''',
'''    # corrected source: panel prices + the same average cost as the dashboard
    pnl_rows = []
    try:
        import pnl as _pnl
        pnl_rows = _pnl.build().get("positions", [])
    except Exception:
        pnl_rows = []
    if pnl_rows:
        for row in pnl_rows:
            sym = row["symbol"]
            if sym in MANUAL_ONLY:
                continue
            out["positions"].append({**row})
            if row["status"] == "hold" or not place:
                continue
            px = float(row["price"])
            qty = int(row["qty"])
            try:
                import tsetmc as api
                inst = api.resolve(sym)
                isin = str((inst or {}).get("isin") or "")
            except Exception:
                isin = ""
            res = _sell(sym, isin, qty, px) if isin else {"message": "no_isin"}
            msg = str(res.get("message") or "")
            rec = {"at": out["at"], "symbol": sym, "qty": qty, "price": px,
                   "reason": "sell_tp" if row["status"] == "take_profit" else "sell_sl",
                   "pnl_pct": row["pnl_pct"], "message": msg,
                   "submitted": bool("موفق" in msg or "ثبت" in msg)}
            out["exits"].append(rec)
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\\n")
        STATE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        if out["exits"] and notify:
            try:
                from tsebot.alerts import send
                lines = [f"زمان: {out['at']}", ""]
                for x in out["exits"]:
                    st = "✔ ثبت شد" if x["submitted"] else "✘ ثبت نشد"
                    lines.append(f"{x['symbol']} | {x['reason']} | {x['pnl_pct']:+.2f}% | "
                                 f"قیمت {x['price']:,.0f} | {st}")
                send("[WarRobot] خروج خودکار (حد سود/ضرر)", "\\n".join(lines),
                     key="exit-auto", force=True)
            except Exception:
                pass
        triggered = [r["symbol"] for r in out["positions"] if r.get("status") in ("take_profit", "stop_loss")]
        print(json.dumps({"at": out["at"], "positions": len(out["positions"]),
                          "triggered": triggered, "exits": len(out["exits"])}, ensure_ascii=False))
        return 0

    for pos in positions():
        sym = pos["symbol"]''')
    ie.write_text(e, encoding="utf-8")
    print("intraday_exit uses the corrected P/L")
else:
    print("intraday_exit already wired")

# --------------------------------------- 3) refresh pnl every 3 min in session
sch = WAR / "scheduler.py"
t = sch.read_text(encoding="utf-8")
if "pnl.py" not in t:
    t = t.replace('''    ("08:46", "09:45", 5, "ordermon", "order_monitor.py"),''',
'''    ("08:46", "09:45", 5, "ordermon", "order_monitor.py"),
    ("09:00", "12:30", 3, "pnlrefresh", "pnl.py"),''')
    sch.write_text(t, encoding="utf-8")
    print("pnl refresh scheduled every 3 minutes")
else:
    print("pnl refresh already scheduled")
