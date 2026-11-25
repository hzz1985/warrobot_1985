"""Fix the stale-price bug that blocked today's pre-open order.

Root cause found on 1405/08/24:
  order_candidates used TSETMC closingPriceDaily as the "live" price, but that
  endpoint returned a 6-week-old row for آواپارس (5140 vs the broker's 8110).
  Consequences:
    * qty was sized for 5140 (152,753 shares) instead of the real price
    * the price guard in flow.js then refused to submit a wrong-priced order
    * allocation was also distorted (holdings valued at the stale price)

Fix:
  1) holdings are valued from the broker's advanced portfolio (state/pnl.json)
     when it is fresh — that is the authoritative number
  2) a stale TSETMC close (> 3 sessions old) is ignored: px/qty become 0 and
     flow.js sizes the order from the panel's live price instead
  3) the log records why a close was rejected (stale_close)
"""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\order_candidates.py")
s = P.read_text(encoding="utf-8")

# ---- 1) helper: fresh broker holdings + freshness check for a TSETMC close ----
if "def _broker_holdings" not in s:
    s = s.replace('''def _isin(symbol: str) -> str:''',
'''def _broker_holdings() -> dict:
    """Market value of each holding from the broker's advanced portfolio."""
    try:
        import json as _json
        from pathlib import Path as _P
        p = _P(__file__).resolve().parent / "state" / "pnl.json"
        d = _json.loads(p.read_text(encoding="utf-8"))
        return {str(r["symbol"]): float(r.get("value") or 0)
                for r in d.get("positions", []) if r.get("value")}
    except Exception:
        return {}


def _close_is_fresh(date_int) -> bool:
    """A TSETMC close older than 3 sessions must not be used for pricing."""
    try:
        import market_cal
        today = market_cal.now_tehran()
        ds = str(int(date_int))
        import jdatetime
        d = jdatetime.date(int(ds[:4]), int(ds[4:6]), int(ds[6:8])).togregorian()
        return (today.date() - d).days <= 5
    except Exception:
        return True


def _isin(symbol: str) -> str:''')
    print("helpers added")

# ---- 2) use broker holdings for allocation ----
s = s.replace('''    held = {sym: val for sym, val in holdings.items() if sym not in MANUAL_ONLY}''',
'''    broker_held = _broker_holdings()
    if broker_held:
        holdings = {**holdings, **broker_held}   # broker value wins over TSETMC
    held = {sym: val for sym, val in holdings.items() if sym not in MANUAL_ONLY}''')

# ---- 3) reject a stale close: leave pricing to the panel ----
s = s.replace('''        live_px = _live_close(sym)
        if live_px > 0:
            px = live_px''',
'''        live_px = _live_close(sym)
        if live_px > 0:
            # only trust it when the series is recent; otherwise let the panel price
            try:
                import tsetmc as _api
                _rows = (_api.closing_daily(_api.resolve(sym)["ins_code"]) or {}).get(
                    "closingPriceDaily") or []
                _last = sorted(_rows, key=lambda r: str(r.get("dEven") or ""))[-1]
                if not _close_is_fresh(_last.get("dEven")):
                    out.setdefault("warnings", []).append(
                        {"symbol": sym, "stale_close": live_px,
                         "last_date": _last.get("dEven")})
                    live_px = 0.0
            except Exception:
                pass
        if live_px > 0:
            px = live_px''')

s = s.replace('''        budget = need_pre * scale
        qty = int(budget / (px * 1.015)) if px > 0 else 0''',
'''        budget = need_pre * scale
        if px <= 0:
            # stale/unknown price: let flow.js size it from the live panel price
            qty = 0
        else:
            qty = int(budget / (px * 1.015))''')

P.write_text(s, encoding="utf-8")
print("order_candidates patched: broker holdings + stale-close guard")
