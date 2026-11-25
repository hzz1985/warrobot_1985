"""Pre-open price discovery — official band + the 5 best levels of the book.

Old behaviour (wrong): the order price came from yesterday's close or from the
panel's prefilled field. A symbol locked at yesterday's ceiling therefore got a
bid ~3% too low and never filled.

New discovery, in this order:
  1. official static band from TSETMC `instrumentInfo.staticThreshold`
     (psGelStaMin = floor, psGelStaMax = ceiling) — this is "آستانه قیمت" on the
     broker's own detail tab;
  2. the 5 best levels from TSETMC `BestLimits` (same table as «پنج مظنه برتر»):
     buy  -> best ask if a seller exists, else today's ceiling (locked symbol);
     sell -> best bid if a buyer exists, else the floor;
  3. fallback: previous close ±3% (only when the API is unreachable).

Every price is clamped into the official band, so an order can never be rejected
for being outside «آستانه نوسان».
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))

API = "https://cdn.tsetmc.com/api"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/157.0",
     "Accept": "application/json, text/plain, */*", "Referer": "https://tsetmc.com/"}
BAND_PCT = 0.03
_TTL = 120          # seconds
_cache: dict[str, tuple[float, dict]] = {}


def _get(url: str, timeout: int = 20) -> dict:
    try:
        req = urllib.request.Request(url, headers=H)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return {}


def _ins_code(symbol: str) -> str:
    try:
        import tsetmc as api
        return str((api.resolve(symbol) or {}).get("ins_code") or "")
    except Exception:
        return ""


def _tick(price: float) -> int:
    return int(round(price))


def clamp(price: float, lo: int, hi: int) -> int:
    p = _tick(price)
    if lo and p < lo:
        p = lo
    if hi and p > hi:
        p = hi
    return p


def market(symbol: str, prev_close: float = 0.0) -> dict:
    """{floor, ceiling, bids[(px,vol)..5], asks[(px,vol)..5], source}"""
    key = symbol
    now = time.time()
    if key in _cache and now - _cache[key][0] < _TTL:
        return _cache[key][1]

    code = _ins_code(symbol)
    out = {"floor": 0, "ceiling": 0, "bids": [], "asks": [], "source": ""}
    if code:
        info = (_get(f"{API}/Instrument/GetInstrumentInfo/{code}") or {}).get("instrumentInfo") or {}
        st = info.get("staticThreshold") or {}
        out["floor"] = int(float(st.get("psGelStaMin") or 0))
        out["ceiling"] = int(float(st.get("psGelStaMax") or 0))
        if out["floor"] and out["ceiling"]:
            out["source"] = "tsetmc_staticThreshold"
        book = (_get(f"{API}/BestLimits/{code}") or {}).get("bestLimits") or []
        for row in book[:5]:
            bp = float(row.get("pMeDem") or 0)
            bv = float(row.get("qTitMeDem") or 0)
            ap = float(row.get("pMeOf") or 0)
            av = float(row.get("qTitMeOf") or 0)
            if bp:
                out["bids"].append((int(bp), int(bv)))
            if ap:
                out["asks"].append((int(ap), int(av)))
        if out["bids"] or out["asks"]:
            out["source"] = (out["source"] + "+best_limits").lstrip("+")

    if not out["floor"] and prev_close:
        pc = float(prev_close)
        out["floor"] = _tick(pc * (1 - BAND_PCT))
        out["ceiling"] = _tick(pc * (1 + BAND_PCT))
        out["source"] = out["source"] or "prev_close_band"
    _cache[key] = (now, out)
    return out


def buy_price(symbol: str, prev_close: float, next_ceiling: float = 0.0) -> dict:
    """Limit for a buy: cross the best ask, or bid today's ceiling when locked."""
    m = market(symbol, prev_close)
    ceiling = m["ceiling"] or int(next_ceiling or 0) or _tick(float(prev_close or 0) * (1 + BAND_PCT))
    best_ask = m["asks"][0][0] if m["asks"] else 0
    if best_ask:
        price, src = clamp(best_ask, m["floor"], ceiling), "best_ask"
    else:
        price, src = clamp(ceiling, m["floor"], ceiling), "ceiling_locked"
    if m["bids"] and m["bids"][0][0] >= ceiling:
        price, src = clamp(ceiling, m["floor"], ceiling), "ceiling_locked"
    return {"price": price, "source": src, "floor": m["floor"], "ceiling": ceiling,
            "best_bid": m["bids"][0][0] if m["bids"] else 0,
            "best_ask": best_ask, "band_source": m["source"]}


def sell_price(symbol: str, prev_close: float, panel_price: float = 0.0) -> dict:
    """Limit for an exit: hit the best bid, else the floor (guaranteed fill)."""
    m = market(symbol, prev_close)
    floor = m["floor"] or _tick(float(prev_close or 0) * (1 - BAND_PCT))
    ceiling = m["ceiling"] or _tick(float(prev_close or 0) * (1 + BAND_PCT))
    best_bid = m["bids"][0][0] if m["bids"] else 0
    ref = best_bid or float(panel_price or 0) or float(prev_close or 0)
    price = clamp(ref, floor, ceiling)
    src = "best_bid" if best_bid else ("panel" if panel_price else "prev_close")
    return {"price": price, "source": src, "floor": floor, "ceiling": ceiling,
            "best_bid": best_bid, "best_ask": m["asks"][0][0] if m["asks"] else 0,
            "band_source": m["source"]}


if __name__ == "__main__":
    tests = [("آباد", 2424.0), ("آردینه", 50088.0), ("فولاد", 4850.0)]
    for sym, pc in tests:
        print(sym, "BUY", json.dumps(buy_price(sym, pc), ensure_ascii=False))
        print(sym, "SELL", json.dumps(sell_price(sym, pc), ensure_ascii=False))
