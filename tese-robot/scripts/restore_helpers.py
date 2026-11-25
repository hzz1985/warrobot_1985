"""Re-insert the helper functions that the chart rewrite accidentally deleted."""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\app.py")
src = P.read_text(encoding="utf-8")

HELPERS = r'''
def _norm(sym: str) -> str:
    """Strip board suffixes so "شپاس1" and "شپاس" map to the same position."""
    s = str(sym or "").strip()
    while s and (s[-1].isdigit() or s[-1] in "ح"):
        s = s[:-1]
    return s


def _marks() -> Dict[str, Dict[str, Any]]:
    """Live prices written by the engine on every scan (state/marks.json)."""
    data = _read_json(CFG.path("state_dir") / "marks.json", {})
    return {_norm(k): v for k, v in (data or {}).items() if isinstance(v, dict)}


def live_prices() -> Dict[str, float]:
    out: Dict[str, float] = {}
    for sym, m in _marks().items():
        try:
            v = float(m.get("last") or 0)
        except Exception:
            v = 0.0
        if v > 0:
            out[sym] = v
    return out


def _fetch_live_once() -> Dict[str, float]:
    """One refresh attempt; the engine owns the real source, so this is best-effort."""
    return live_prices()


def _refresh_live_forever() -> None:
    import time as _t
    while True:
        try:
            _fetch_live_once()
        except Exception:
            pass
        _t.sleep(60)


def _last_close(symbol: str) -> float | None:
    """Last daily close from the bot's own data folder."""
    try:
        import pandas as _pd
        p = CFG.path("data_dir") / "daily" / f"{symbol}_daily.csv"
        if not p.exists():
            return None
        d = _pd.read_csv(p)
        return float(d["Close"].iloc[-1])
    except Exception:
        return None


def _cost_basis() -> Dict[str, float]:
    data = _read_json(CFG.path("state_dir") / "cost_basis.json", {})
    return {_norm(k): float(v) for k, v in (data or {}).items() if v}


def open_positions() -> List[Dict[str, Any]]:
    """Open positions from the ledger, marked to the latest known price."""
    led = _ledger()
    prices = live_prices()
    basis = _cost_basis()
    out: List[Dict[str, Any]] = []
    for pos in led.open_positions():
        sym = _norm(pos.symbol)
        qty = float(pos.qty)
        avg = float(pos.avg_cost or basis.get(sym) or 0.0)
        cost = qty * avg
        last = prices.get(sym) or _last_close(pos.symbol) or avg
        pnl_pct = ((last / avg) - 1) * 100 if avg else 0.0
        out.append({
            "symbol": pos.symbol, "qty": int(qty), "avg": round(avg, 1),
            "cost": round(cost, 0), "price": round(float(last), 1),
            "value": round(float(last) * qty, 0),
            "pnl_pct": round(pnl_pct, 2),
            "total_pnl": round(float(last - avg) * qty, 0),
            "total_pct": round(pnl_pct, 2),
            "share_pct": 0.0,
        })
    total = sum(p["value"] for p in out) or 1.0
    for p in out:
        p["share_pct"] = round(p["value"] / total * 100, 1)
    return out


def decide_label(pnl: float, total: float, cfg) -> str:
    """What the bot would do with this position right now."""
    try:
        target = float(cfg.get("sell.target_pct", 6.0))
        stop = float(cfg.get("sell.stop_pct", 3.0))
    except Exception:
        target, stop = 6.0, 3.0
    if total <= -stop or pnl <= -stop:
        return "فروش (حد ضرر)"
    if total >= target or pnl >= target:
        return "فروش (حد سود)"
    return "نگهداری"


'''

marker = "def health():"
i = src.index(marker)
src = src[:i] + HELPERS + src[i:]
P.write_text(src, encoding="utf-8")
print("helpers restored")
