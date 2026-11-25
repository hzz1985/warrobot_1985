# -*- coding: utf-8 -*-
"""Burst As Exit — intraday deterioration detector for held positions.

The structural risk in TSE is not the entry, it is the exit: once a symbol locks
at the floor (sell-queue) you cannot sell, and the profit of a multi-day queue
evaporates. Price alone reacts too late, so this module reads the tape:

  flk   signed volume flow (aggressive buying vs selling)
  flc   buy/sell volume imbalance
  hs30  share of volume printed at the day's high
  queue_broken  price printed below the day's high after having been locked

A position that was riding a buy-queue is exited as soon as the tape turns,
without waiting for the -3% stop.
"""
from __future__ import annotations

import time
from typing import Dict, Optional

from .calendar_ir import jalali_str, now_tehran
from .queue_features import burst_features

_CACHE: Dict[str, tuple] = {}


def _fetch_today(symbol: str, jal: str):
    import algotik_tse as att
    df = att.get_trades(symbol, start=jal, end=jal, progress=False)
    if df is None or len(df) < 5:
        return None
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if "Canceled" in df.columns:
        df["Canceled"] = df["Canceled"].fillna(False).astype(bool)
        df = df[~df["Canceled"]]
    if "Timestamp" not in df.columns and "Time" in df.columns:
        df["Timestamp"] = df["Time"]
    return df.sort_values("Timestamp") if "Timestamp" in df.columns else df


def assess(cfg, symbol: str, refresh_sec: Optional[float] = None) -> Optional[dict]:
    be = (cfg.section("sell").get("burst_exit") or {})
    if not be.get("enabled", True):
        return None
    ttl = float(refresh_sec if refresh_sec is not None else be.get("refresh_sec", 180))
    now = time.time()
    hit = _CACHE.get(symbol)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        g = _fetch_today(symbol, jalali_str(now_tehran()))
    except Exception as exc:
        return {"error": str(exc)[:120]}
    if g is None:
        return None
    bfe = burst_features(g)
    if bfe is None:
        return None
    price = g["Price"].astype(float)
    last = float(price.iloc[-1])
    peak = float(price.max())
    # locked now: the last prints sit at the day's high and no lower print after
    tail = price.tail(30)
    locked_now = bool((tail >= peak - 1e-9).all())
    touched_high = bool(peak > 0 and (price >= peak - 1e-9).any())
    queue_broken = bool(touched_high and last < peak - 1e-9)
    out = {
        "symbol": symbol,
        "last": last,
        "peak": peak,
        "locked_now": locked_now,
        "queue_broken": queue_broken,
        "distance_from_peak_pct": round((last / peak - 1) * 100, 3) if peak else 0.0,
        **{k: bfe.get(k) for k in ("flk", "flc", "hs30", "pv30", "volM", "nb1")},
    }
    _CACHE[symbol] = (now, out)
    return out


def should_exit(cfg, symbol: str, pnl_pct: float, was_locked: bool) -> Optional[dict]:
    """Return a reason dict if the tape says 'leave now', else None."""
    be = (cfg.section("sell").get("burst_exit") or {})
    if not be.get("enabled", True):
        return None
    a = assess(cfg, symbol)
    if not a or a.get("error"):
        return None
    min_profit = float(be.get("min_profit_pct", 1.5))
    if pnl_pct < min_profit:
        return None
    # 1) the queue we were riding just broke
    if be.get("exit_on_queue_break_tape", True) and was_locked and a["queue_broken"]:
        return {"reason": "burst_queue_break", **a}
    # 2) aggressive flow flipped negative while price is still near the high
    min_flk = float(be.get("min_flk", -0.25))
    if a.get("flk") is not None and a["flk"] < min_flk and a["distance_from_peak_pct"] > -0.8:
        return {"reason": "burst_flow_reversal", **a}
    # 3) buy-side imbalance collapsed at the ceiling
    min_flc = float(be.get("min_flc", 0.0))
    if (a.get("flc") is not None and a["flc"] < min_flc
            and (a.get("hs30") or 0) > 0.4 and was_locked):
        return {"reason": "burst_imbalance_drop", **a}
    return None
