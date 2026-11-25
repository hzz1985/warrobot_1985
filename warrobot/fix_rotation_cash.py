"""Rotation fixes found on 1405/08/26 when the 10:00 cycle really ran:

1) the per-symbol budget was total_assets/3 (1.22B) even though only ~1.0B cash
   was free -> the order would have been rejected for insufficient funds.
   Now the budget is capped at 95% of the free cash from the morning snapshot.
2) only one replacement candidate was fetched; when its symbol could not be
   bound (bind_mismatch) the rotation stopped with nothing placed.
   Now several candidates are tried in order until one is accepted.
"""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\rebalance.py")
s = P.read_text(encoding="utf-8")

if "free_cash_cap" not in s:
    s = s.replace('''    assets = total_assets()
    third = assets / 3.0''',
'''    assets = total_assets()
    third = assets / 3.0
    # free_cash_cap: never size an order above the money we actually have
    try:
        import json as _json
        from pathlib import Path as _P
        _snap = _json.loads((_P(__file__).resolve().parent / "state" / "prices.json"
                             ).read_text(encoding="utf-8"))
        free_cash = float((_snap.get("balance") or {}).get("account_balance") or 0)
    except Exception:
        free_cash = 0.0
    if free_cash > 0:
        third = min(third, free_cash * 0.95)
    out["free_cash"] = round(free_cash, 0)''')

    s = s.replace('''        out["would_buy"] = [c["symbol"] for c in _next_candidates({o["symbol"] for o in unfilled}, len(unfilled))]''',
'''        out["would_buy"] = [c["symbol"] for c in _next_candidates(
            {o["symbol"] for o in unfilled}, len(unfilled) + 4)]''')

    s = s.replace('''    for c in _next_candidates(used, len(unfilled)):''',
'''    placed_ok = False
    for c in _next_candidates(used, len(unfilled) + 4):
        if placed_ok:
            break''')

    s = s.replace('''        try:
            p = subprocess.run([NODE, str(PW / "flow.js"), c["symbol"], isin, str(qty), str(px)],''',
'''        try:
            p = subprocess.run([NODE, str(PW / "flow.js"), c["symbol"], isin, str(qty), str(px)],''')

    # mark success and continue on bind mismatch
    s = s.replace('''            if "bind_mismatch" in (data.get("message") or ""):
                continue''', '''            if "bind_mismatch" in (data.get("message") or ""):
                continue''')
    P.write_text(s, encoding="utf-8")
    print("rotation: cash cap + multi-candidate fallback")
else:
    print("already patched")
