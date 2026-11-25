"""Remaining fixes for the recurring problems.

  1. switch pm2 warrobot -> daemon_service (old daemon no longer schedules)
  2. ready_check/preflight/order skip closed days entirely (no emails, no logins)
  3. ready_check emails are one-shot per slot per day (no duplicates)
  4. order_candidates: equal allocation (each symbol = total assets / 3, minus
     what is already held in that symbol)
"""
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")

# ------------------------------------------------------- 2+3) readiness email
rc = WAR / "ready_check.py"
s = rc.read_text(encoding="utf-8")
if "market_cal" not in s:
    s = s.replace("from tsebot.alerts import send  # noqa: E402  (smtp-backed, same channel as the daemon)",
                  "from tsebot.alerts import send  # noqa: E402  (smtp-backed, same channel as the daemon)\nimport market_cal  # noqa: E402")
    s = s.replace('''def main() -> int:
    snap = _snapshot()''',
'''def main() -> int:
    # closed day: no login attempt, no email
    if not market_cal.market_day():
        result = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "skipped": market_cal.reason_closed(), "ready": None, "emailed": False}
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0

    snap = _snapshot()''')
    s = s.replace('''    try:
        send(subject, body, key=f"ready-{stamp}", force=True)
        result["emailed"] = True
    except Exception as exc:
        result["emailed"] = False
        result["email_error"] = str(exc)[:120]''',
'''    slot = "ready-" + stamp[:2] + ":" + stamp[3:5]
    result["emailed"] = market_cal.send_once(slot, subject, body)''')
    rc.write_text(s, encoding="utf-8")
    print("ready_check: closed-day guard + one-shot emails")
else:
    print("ready_check already guarded")

# ------------------------------------------------------------- preflight guard
pf = WAR / "preflight.py"
p = pf.read_text(encoding="utf-8")
if "market_cal" not in p:
    p = p.replace("import pandas as pd  # noqa: E402",
                  "import pandas as pd  # noqa: E402\nimport market_cal  # noqa: E402")
    p = p.replace('''def main() -> int:
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}''',
'''def main() -> int:
    if not market_cal.market_day():
        out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "skipped": market_cal.reason_closed(), "ready": None}
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False))
        return 0
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}''')
    pf.write_text(p, encoding="utf-8")
    print("preflight: closed-day guard")
else:
    print("preflight already guarded")

# --------------------------------------- 4) equal allocation in order_candidates
oc = WAR / "order_candidates.py"
o = oc.read_text(encoding="utf-8")
if "equal_allocation" not in o:
    o = o.replace('''    # one third of TOTAL assets per symbol (cash + holdings), capped by free cash
    try:
        import rebalance as _reb
        total = _reb.total_assets()
    except Exception:
        total = free
    third = total / 3.0 if total > 0 else free * 0.95 / len(df)
    budget = min(third, free * 0.95)
    out["total_assets"] = round(total, 0)
    out["per_symbol_budget"] = round(budget, 0)''',
'''    # equal allocation: every symbol should end up at total_assets / 3.
    # We subtract what is already held in that symbol, so a top-up only adds the
    # missing part and the portfolio converges to an equal split.
    import market_cal as _mc  # noqa: F401  (kept for closed-day helpers)
    holdings = {}
    try:
        import portfolio_sync as _ps
        for pos in _ps.broker_positions():
            holdings[str(pos["symbol"])] = float(pos.get("qty") or 0) * float(pos.get("avg") or 0)
    except Exception:
        holdings = {}
    try:
        import rebalance as _reb
        total = _reb.total_assets()
    except Exception:
        total = free
    target_each = (total / 3.0) if total > 0 else free / 3.0
    out["total_assets"] = round(total, 0)
    out["target_per_symbol"] = round(target_each, 0)
    out["equal_allocation"] = True''')
    o = o.replace('''    budget = min(third, free * 0.95)
    out["total_assets"] = round(total, 0)
    out["per_symbol_budget"] = round(budget, 0)''', '')
    o = o.replace('''        qty = int(budget / (px * 1.015)) if px > 0 else 0''',
'''        # equal allocation: only buy the missing part of this symbol's share
        already = holdings.get(sym, 0.0)
        need = max(0.0, target_each - already)
        budget = min(need, free * 0.95)
        qty = int(budget / (px * 1.015)) if px > 0 else 0''')
    oc.write_text(o, encoding="utf-8")
    print("order_candidates: equal allocation (target = total/3 per symbol)")
else:
    print("equal allocation already present")
