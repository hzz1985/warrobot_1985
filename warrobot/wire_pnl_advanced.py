"""Point pnl.py at the broker's advanced portfolio — the exact numbers the
operator sees in the panel (real average cost, real P/L)."""
from pathlib import Path

p = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot") / "pnl.py"
s = p.read_text(encoding="utf-8")

if "advanced_portfolio_values.js" not in s:
    # replace the panel reader with the advanced one
    s = s.replace('''def panel_positions() -> list[dict]:
    import pw_lock
    with pw_lock.guard("pnl_panel") as got:
        if not got:
            return []
        try:
            p = subprocess.run([NODE, str(PW / "portfolio_values.js")], cwd=str(PW),
                               capture_output=True, text=True, timeout=600,
                               encoding="utf-8", errors="replace")
            for line in reversed((p.stdout or "").splitlines()):
                if line.strip().startswith("{"):
                    return json.loads(line).get("positions") or []
        except Exception:
            pass
    return []''',
'''def panel_positions() -> list[dict]:
    """Broker's advanced portfolio: real average cost + exact P/L (authoritative)."""
    import pw_lock
    with pw_lock.guard("pnl_panel") as got:
        if not got:
            return []
        try:
            p = subprocess.run([NODE, str(PW / "advanced_portfolio_values.js")],
                               cwd=str(PW), capture_output=True, text=True, timeout=600,
                               encoding="utf-8", errors="replace")
            for line in reversed((p.stdout or "").splitlines()):
                if line.strip().startswith("{"):
                    return json.loads(line).get("positions") or []
        except Exception:
            pass
    return []''')

    # build(): the advanced source already provides avg_cost and pnl_pct
    s = s.replace('''    rows = []
    for p in panel_positions():
        sym = p["symbol"]
        qty = int(p["qty"])
        price = float(p["price"])
        if sym in manual:''',
'''    rows = []
    for p in panel_positions():
        sym = p["symbol"]
        qty = int(p["qty"])
        price = float(p["price"])
        broker_pnl_pct = p.get("pnl_pct")
        broker_pnl_rial = p.get("pnl_rial")
        broker_avg = float(p.get("avg_cost") or 0)
        if sym in manual:''')
    s = s.replace('''        elif broker_avg.get(sym, 0) > 0:
            avg, src = broker_avg[sym], "broker"
        elif ledger_avg.get(sym, 0) > 0:
            avg, src = ledger_avg[sym], "ledger"
        else:
            avg, src = 0.0, "unknown"
        pnl = ((price / avg) - 1) * 100 if avg > 0 else 0.0''',
'''        elif broker_avg > 0:
            avg, src = broker_avg, "broker"
        elif broker_avg.get(sym, 0) > 0:
            avg, src = broker_avg[sym], "broker"
        elif ledger_avg.get(sym, 0) > 0:
            avg, src = ledger_avg[sym], "ledger"
        else:
            avg, src = 0.0, "unknown"
        # the panel's own P/L is authoritative when present (it is fee-inclusive)
        pnl = float(broker_pnl_pct) if broker_pnl_pct is not None else (((price / avg) - 1) * 100 if avg > 0 else 0.0)''')
    s = s.replace('''            "pnl_rial": round((price - avg) * qty, 0) if avg else 0,''',
'''            "pnl_rial": float(broker_pnl_rial) if broker_pnl_rial is not None else (round((price - avg) * qty, 0) if avg else 0),
            "avg_cost_fee": p.get("avg_cost_fee"),
            "pnl_source": "broker_advanced" if broker_pnl_pct is not None else "computed",''')
    p.write_text(s, encoding="utf-8")
    print("pnl.py now reads the broker advanced portfolio")
else:
    print("already wired")
