"""Make the equal-weight target robust: broker values + cash, no browser calls.

Symptom seen twice today: the allocation collapsed to a tiny target because
`rebalance.total_assets()` returned cash only (its browser read hits the same
profile lock). The planner then wanted to buy several symbols with a target of
158M instead of ~1.2B.

New rule (pure local data, no Playwright):
    total_assets = free cash + sum(value of every holding from state/pnl.json)
state/pnl.json is refreshed by the broker panel and already carries the exact
market values, so this is both cheaper and more reliable.
"""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\order_candidates.py")
s = P.read_text(encoding="utf-8")

old = '''    try:
        import rebalance as _reb
        total = _reb.total_assets()
    except Exception:
        total = free
    target_each = (total / 3.0) if total > 0 else free / 3.0'''

new = '''    # total assets from local authoritative data (no browser round trip)
    broker_values = _broker_holdings()
    if broker_values:
        total = free + sum(broker_values.values())
        holdings = dict(broker_values)
    else:
        total = free + sum(holdings.values())
        if total <= free:                     # nothing held -> last resort
            try:
                import rebalance as _reb
                total = max(total, _reb.total_assets())
            except Exception:
                pass
    target_each = (total / 3.0) if total > 0 else free / 3.0'''

if old in s:
    s = s.replace(old, new, 1)
    P.write_text(s, encoding="utf-8")
    print("total_assets now = cash + broker panel values")
else:
    print("pattern not found (already patched?)")
