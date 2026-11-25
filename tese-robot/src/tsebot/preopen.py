"""Pre-open / queue analysis for the buy side.

The model ranks symbols by the probability of a durable buy-queue, but a ranking
alone is not enough: at 08:30 the decision depends on the actual order book —
how much demand is stacked at the ceiling, whether anyone is offering at the
ceiling, and how lopsided the book is. These are the "missing parameters" that
the setup needs and that the daily model does not see.
"""
from typing import Any, Dict, Optional

from .config import Config


def _norm(sym: Any) -> str:
    return (str(sym).replace("ي", "ی").replace("ك", "ک")
            .replace("\u200c", "").replace(" ", "").strip())


def queue_metrics(snap: Optional[Dict[str, Any]], symbol: str) -> Optional[Dict[str, Any]]:
    """Order-book metrics for one symbol from a market snapshot."""
    if not snap:
        return None
    stocks = snap.get("stocks")
    book = snap.get("order_book")
    if stocks is None or len(stocks) == 0:
        return None
    try:
        match = stocks[stocks["Symbol"].map(_norm) == _norm(symbol)]
    except Exception:
        return None
    if len(match) == 0:
        return None
    row = match.iloc[0]

    def f(key: str) -> Optional[float]:
        try:
            v = float(row.get(key))
            return v if v > 0 else None
        except Exception:
            return None

    ceiling = f("MaxAllowed")
    floor = f("MinAllowed")
    last = f("Close") or f("Last")
    ins = row.get("InsCode")

    bid_at_ceiling = ask_at_ceiling = 0.0
    bid_total = ask_total = 0.0
    bid_count = ask_count = 0
    if book is not None and len(book) and ins is not None:
        try:
            levels = book[book["InsCode"] == ins]
        except Exception:
            levels = None
        if levels is not None:
            for _, lv in levels.iterrows():
                try:
                    bp = float(lv.get("BidPrice") or 0)
                    bv = float(lv.get("BidVolume") or 0)
                    ap = float(lv.get("AskPrice") or 0)
                    av = float(lv.get("AskVolume") or 0)
                except Exception:
                    continue
                bid_total += bv
                ask_total += av
                bid_count += int(lv.get("BidOrderCount") or 0)
                ask_count += int(lv.get("AskOrderCount") or 0)
                if ceiling and bp >= ceiling * 0.999:
                    bid_at_ceiling += bv
                if ceiling and ap and ap <= ceiling * 1.001 and av > 0:
                    ask_at_ceiling += av

    total = bid_total + ask_total
    imbalance = (bid_total / total) if total > 0 else 0.0
    dist_pct = ((ceiling - last) / last * 100.0) if (ceiling and last) else 99.0
    return {
        "symbol": symbol,
        "last": last,
        "ceiling": ceiling,
        "floor": floor,
        "dist_to_ceiling_pct": round(dist_pct, 3),
        "bid_at_ceiling": bid_at_ceiling,
        "ask_at_ceiling": ask_at_ceiling,
        "bid_total": bid_total,
        "ask_total": ask_total,
        "imbalance": round(imbalance, 3),
        "queue_value_rial": round(bid_at_ceiling * (ceiling or last or 0), 0),
        "bid_orders": bid_count,
        "ask_orders": ask_count,
        "at_ceiling": bool(ceiling and last and last >= ceiling * 0.999),
    }


def confirm(cfg: Config, m: Optional[Dict[str, Any]]) -> tuple:
    """Decide whether the order book supports a queue entry.

    Returns (ok, reason, metrics). When the filter is disabled everything passes.
    """
    q = (cfg.section("buy").get("preopen_queue") or {})
    if not q.get("enabled", False):
        return True, "disabled", m or {}
    if not m:
        return False, "no_book", {}
    reasons = []
    if q.get("require_at_ceiling", True) and not m.get("at_ceiling"):
        if float(m.get("dist_to_ceiling_pct", 99)) > float(q.get("max_dist_to_ceiling_pct", 1.0)):
            reasons.append("not_at_ceiling")
    if q.get("require_no_ask_at_ceiling", True) and float(m.get("ask_at_ceiling", 0)) > 0:
        reasons.append("supply_at_ceiling")
    if float(m.get("queue_value_rial", 0)) < float(q.get("min_queue_value_rial", 0)):
        reasons.append("thin_queue")
    if float(m.get("imbalance", 0)) < float(q.get("min_imbalance", 0)):
        reasons.append("weak_imbalance")
    return (not reasons), (",".join(reasons) if reasons else "ok"), m
