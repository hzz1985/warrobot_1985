"""Proportional cash allocation for the equal-weight top-up.

Bug: each symbol's budget was min(need, free*0.95), so every symbol could claim
95% of the free cash — the sum far exceeded what is available and the later
symbols in the loop would hit rejected orders.

Now the needs are computed first, and the free cash is shared proportionally:
    scale = min(1, free*0.95 / sum(all needs))
    budget_i = need_i * scale
So the total of the orders never exceeds the free cash, and each symbol moves
toward its 1/3 target by the same proportion.
"""
from pathlib import Path

p = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot") / "order_candidates.py"
s = p.read_text(encoding="utf-8")

if "proportional allocation" not in s:
    # 1) pre-compute the needs (one pass over the candidates)
    s = s.replace('''    for _, r in df.iterrows():
        sym = str(r["symbol"])
        isin = str(r.get("isin") or "") or _isin(sym)''',
'''    # ---- proportional allocation: compute every need, then scale to the cash
    needs = {}
    for _, r in df.iterrows():
        sym = str(r["symbol"])
        try:
            px = _live_close(sym) or float(r.get("close") or 0)
        except Exception:
            px = 0.0
        already = holdings.get(sym, 0.0)
        needs[sym] = (px, max(0.0, target_each - already))
    total_need = sum(n for _, n in needs.values())
    scale = min(1.0, (free * 0.95) / total_need) if total_need > 0 else 0.0
    out["allocation_scale"] = round(scale, 4)
    out["total_need"] = round(total_need, 0)
    # proportional allocation

    for _, r in df.iterrows():
        sym = str(r["symbol"])
        isin = str(r.get("isin") or "") or _isin(sym)''')
    # 2) use the scaled need inside the loop
    s = s.replace('''        # equal allocation: only buy the missing part of this symbol's share
        already = holdings.get(sym, 0.0)
        need = max(0.0, target_each - already)
        budget = min(need, free * 0.95)
        qty = int(budget / (px * 1.015)) if px > 0 else 0''',
'''        # equal allocation (proportional): this symbol's share of the free cash
        px_pre, need_pre = needs.get(sym, (0.0, 0.0))
        if px <= 0:
            px = px_pre
        budget = need_pre * scale
        qty = int(budget / (px * 1.015)) if px > 0 else 0''')
    p.write_text(s, encoding="utf-8")
    print("proportional allocation applied")
else:
    print("already applied")
