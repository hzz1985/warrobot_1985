"""Equal-weight engine, final logic: top up the holdings first.

The proportional pass was correct arithmetically but wrong in strategy: it spread
the cash over six candidates, including symbols not held at all. The operator's
rule is "each portfolio symbol = 1/3 of total assets", so the order of work is:

  1. compute the target (total assets / 3)
  2. take the symbols that are ACTUALLY held (broker portfolio, manual-only excluded)
  3. give each of them its missing amount
  4. share the free cash proportionally between them
  5. only when a position slot is still free AND the cash covers a full target,
     open a new position from the scan list

This keeps the portfolio at three equal sleeves instead of scattering six small
orders.
"""
from pathlib import Path

p = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot") / "order_candidates.py"
s = p.read_text(encoding="utf-8")

if "holdings_first" not in s:
    # replace the whole "needs" pre-computation block with a holdings-first plan
    old_block_start = s.find("    # ---- proportional allocation: compute every need, then scale to the cash")
    old_block_end = s.find("    for _, r in df.iterrows():", old_block_start)
    plan = '''    # ---- holdings-first equal weight (holdings_first) --------------------
    MANUAL_ONLY = {"افران"}
    held = {sym: val for sym, val in holdings.items() if sym not in MANUAL_ONLY}
    slots_free = max(0, 3 - len(held))
    needs = {}
    for sym in held:
        needs[sym] = max(0.0, target_each - held[sym])
    # a new position only if a slot is free and the cash can carry a full target
    if slots_free > 0 and free >= target_each * 0.9:
        for _, r in df.iterrows():
            sym = str(r["symbol"])
            if sym in held or sym in MANUAL_ONLY or sym in needs:
                continue
            needs[sym] = target_each
            slots_free -= 1
            if slots_free == 0:
                break
    total_need = sum(needs.values())
    cash_available = free * 0.95
    scale = min(1.0, cash_available / total_need) if total_need > 0 else 0.0
    out["allocation_scale"] = round(scale, 4)
    out["total_need"] = round(total_need, 0)
    out["planned_symbols"] = list(needs)
    needs = {k: v for k, v in needs.items() if v > 0}

'''
    s = s[:old_block_start] + plan + s[old_block_end:]

    # the loop now walks the planned symbols
    s = s.replace('''    for _, r in df.iterrows():
        sym = str(r["symbol"])
        isin = str(r.get("isin") or "") or _isin(sym)''',
'''    rows_by_symbol = {str(r["symbol"]): r for _, r in df.iterrows()}
    for sym in list(needs):
        r = rows_by_symbol.get(sym)
        if r is None:
            continue
        isin = str(r.get("isin") or "") or _isin(sym)''')
    p.write_text(s, encoding="utf-8")
    print("holdings-first equal weight applied")
else:
    print("already applied")
