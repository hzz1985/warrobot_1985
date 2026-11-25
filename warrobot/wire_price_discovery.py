"""Use today's ceiling (pre-open discovery) for buy orders."""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\order_candidates.py")
s = P.read_text(encoding="utf-8")

anchor = '''        # equal allocation (proportional): this symbol's share of the free cash
        need_pre = needs.get(sym, 0.0)'''

new = '''        # ---- pre-open price discovery ------------------------------------
        # the old chain used yesterday's close / panel prefill, so a symbol
        # locked at yesterday's ceiling was bid ~3% too low and never filled.
        price_source = "close"
        try:
            import price_discovery as _pd
            _prev = 0.0
            try:
                _prev = float(r.get("close") or 0)
            except Exception:
                _prev = 0.0
            if _prev <= 0:
                _prev = _nightly_close().get(sym, 0.0) or px
            _nc = 0.0
            try:
                _nc = float(r.get("next_ceiling") or 0)
            except Exception:
                _nc = 0.0
            _disc = _pd.buy_price(sym, _prev, _nc)
            if _disc["price"] > 0:
                px = float(_disc["price"])
                price_source = _disc["source"]
        except Exception as exc:
            out.setdefault("warnings", []).append(
                {"symbol": sym, "price_discovery_error": str(exc)[:80]})

        # equal allocation (proportional): this symbol's share of the free cash
        need_pre = needs.get(sym, 0.0)'''

if "price_discovery" not in s:
    s = s.replace(anchor, new, 1)
    s = s.replace('''               "price": px, "qty": qty}''',
                  '''               "price": px, "qty": qty, "price_source": price_source}''')
    P.write_text(s, encoding="utf-8")
    print("order_candidates uses pre-open ceiling discovery")
else:
    print("already wired")
