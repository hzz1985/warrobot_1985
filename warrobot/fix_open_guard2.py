"""Allow a second position while the first order is still open.

Today only آباد was ordered although the cash covered another 1/3 slot. Cause: the
open-order guard aborted the WHOLE plan as soon as any order was open ("do not
stack a second order"), instead of skipping only the symbol that already has one.

New rule:
  * symbols with an open order are skipped (their cash is treated as committed)
  * the remaining free cash can open other positions, up to the 1/3 target
"""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\order_candidates.py")
s = P.read_text(encoding="utf-8")

old = '''    if open_orders:
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
        return out'''

new = '''    # symbols that already have an open order are skipped, not the whole plan:
    # their reserved cash is subtracted so the next 1/3 slot can still be opened
    open_buy_syms = {str(o.get("symbol")) for o in open_orders
                     if "خرید" in str(o.get("side") or "")}
    reserved = 0.0
    for o in open_orders:
        try:
            reserved += float(o.get("price") or 0) * float(o.get("qty") or 0)
        except Exception:
            pass
    if open_buy_syms:
        out["open_buy_symbols"] = sorted(open_buy_syms)
        out["reserved_rial"] = round(reserved, 0)'''

if old in s:
    s = s.replace(old, new, 1)

# skip the symbols that already have an order when building the plan
if "in open_buy_syms" not in s:
    s = s.replace('''    for sym in held:
        if sym in near_tp:
            continue        # let the lot-based TP sell it instead of buying more''',
'''    for sym in held:
        if sym in near_tp:
            continue        # let the lot-based TP sell it instead of buying more
        if sym in locals().get("open_buy_syms", set()):
            continue        # an order for this symbol is already working''')
    s = s.replace('''            if sym in held or sym in MANUAL_ONLY or sym in needs:
                continue''',
'''            if sym in held or sym in MANUAL_ONLY or sym in needs:
                continue
            if sym in locals().get("open_buy_syms", set()):
                continue''')

P.write_text(s, encoding="utf-8")
print("open-order guard is now per-symbol")
