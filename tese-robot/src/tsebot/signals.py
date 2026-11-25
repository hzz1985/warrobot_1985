# -*- coding: utf-8 -*-
"""TSE Bot v2 — transparent signal engine (rules_v2).

Design constraints learned from the audit:
  * only information available at the close of day T is used to rank day T+1;
  * one scoring function serves BOTH live picks and the backtest, so a test
    cannot silently evaluate a different model than the one that trades;
  * scores are a weighted, bounded 0..1 blend of named sub-scores, so every pick
    can be explained feature by feature;
  * no probability claim is made: the number is a rank, and the file says so.

Data comes from daily OHLCV files only. That removes the v1 dependency on an
intraday table that went stale and silently zeroed six model inputs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .calendar_ir import TradingCalendar, from_jalali, now_tehran

MIN_HISTORY = 60
WARMUP = 40


def _clip01(x: float) -> float:
    try:
        x = float(x)
    except Exception:
        return 0.0
    if x != x:  # NaN
        return 0.0
    return max(0.0, min(1.0, x))


# --------------------------------------------------------------- data loading
def load_daily(path: Path) -> Optional[pd.DataFrame]:
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    if df.empty:
        return None
    df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
    need = {"date", "Open", "High", "Low", "Close", "Volume"}
    if not need.issubset(df.columns):
        return None
    df = df[["date", "Open", "High", "Low", "Close", "Volume"]].copy()
    df["date"] = df["date"].astype(str).str.strip()
    for c in ("Open", "High", "Low", "Close", "Volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["Close"]).sort_values("date").reset_index(drop=True)
    return df if len(df) >= MIN_HISTORY else None


def load_panel(data_dir: Path | str) -> Dict[str, pd.DataFrame]:
    d = Path(data_dir)
    out: Dict[str, pd.DataFrame] = {}
    for f in sorted(d.glob("*_daily.csv")):
        sym = f.name[: -len("_daily.csv")]
        df = load_daily(f)
        if df is not None:
            out[sym] = df
    return out


def prep_symbol(df: pd.DataFrame) -> Dict[str, Any]:
    """Column-oriented copy so per-day feature extraction is cheap."""
    return {
        "dates": df["date"].tolist(),
        "pos": {d: i for i, d in enumerate(df["date"].tolist())},
        "o": df["Open"].to_numpy(dtype=float),
        "h": df["High"].to_numpy(dtype=float),
        "l": df["Low"].to_numpy(dtype=float),
        "c": df["Close"].to_numpy(dtype=float),
        "v": df["Volume"].to_numpy(dtype=float),
        "n": len(df),
    }


def prepare_panel(panel: Dict[str, pd.DataFrame]) -> Dict[str, Dict[str, Any]]:
    return {s: prep_symbol(df) for s, df in panel.items()}


# ------------------------------------------------------------------- features
def features_from_prep(p: Dict[str, Any], i: int, limit_pct: float = 5.0
                       ) -> Optional[Dict[str, float]]:
    """Features known at the close of bar ``i``. No forward information."""
    n = p["n"]
    if i < WARMUP or i >= n:
        return None
    o, h, l, c, v = p["o"], p["h"], p["l"], p["c"], p["v"]
    close = float(c[i])
    if close <= 0:
        return None
    opn, high, low, vol = float(o[i]), float(h[i]), float(l[i]), float(v[i])
    prev_close = float(c[i - 1])
    if prev_close <= 0:
        return None

    def ret(k: int) -> float:
        if i - k < 0:
            return 0.0
        base = float(c[i - k])
        return (close / base - 1.0) * 100.0 if base else 0.0

    def rsi(period: int = 14) -> float:
        j0 = max(1, i - period + 1)
        d = np.diff(c[j0 - 1:i + 1])
        if len(d) == 0:
            return 50.0
        gain = float(np.clip(d, 0, None).mean())
        loss = float((-np.clip(d, None, 0)).mean())
        if loss <= 0:
            return 100.0
        rs = gain / loss
        return 100.0 - 100.0 / (1.0 + rs)

    def vmean(k: int) -> float:
        j0 = max(0, i - k)
        seg = v[j0:i]
        return float(seg.mean()) if len(seg) else 0.0

    prev5, prev10, prev20 = vmean(5), vmean(10), vmean(20)
    vol_ratio_5 = float(vol / prev5) if prev5 > 0 else 1.0
    vol_ratio_10 = float(vol / prev10) if prev10 > 0 else 1.0
    vol_ratio_20 = float(vol / prev20) if prev20 > 0 else 1.0

    j0 = max(0, i - 249)
    hi250 = float(h[j0:i + 1].max())
    lo250 = float(l[j0:i + 1].min())
    rng = high - low
    day_range_pct = float(rng / prev_close * 100.0)
    close_pos = float((close - low) / rng) if rng > 0 else 0.5

    consec_down = 0
    k = i
    while k > 0 and c[k] < c[k - 1]:
        consec_down += 1
        k -= 1

    limit_up = prev_close * (1 + limit_pct / 100.0)
    near_ceiling = 1.0 if close >= limit_up * 0.97 else 0.0
    is_locked = 1.0 if (high == low and close > prev_close) else 0.0
    gap_pct = float((opn / prev_close - 1.0) * 100.0)

    return {
        "close": close, "prev_close": prev_close,
        "ret_1d": ret(1), "ret_5d": ret(5), "ret_20d": ret(20),
        "rsi_14": rsi(),
        "vol_ratio_5": vol_ratio_5, "vol_ratio_10": vol_ratio_10,
        "vol_ratio_20": vol_ratio_20,
        "range_pct": day_range_pct, "close_pos": close_pos, "gap_pct": gap_pct,
        "consec_down": float(consec_down),
        "dist_from_high_52w": float((hi250 - close) / close * 100.0),
        "pos_52w": float((close - lo250) / (hi250 - lo250)) if hi250 > lo250 else 0.5,
        "near_ceiling": near_ceiling, "is_locked": is_locked,
        "limit_up": limit_up, "is_base_market": 0.0,
    }


def features_at(df: pd.DataFrame, i: int, limit_pct: float = 5.0):
    return features_from_prep(prep_symbol(df), i, limit_pct)


def score_features(f: Dict[str, float], w: Dict[str, float]) -> float:
    """Bounded weighted blend. Every term is 0..1 and documented."""
    parts: List[Tuple[float, float]] = []

    # quiet volume before a queue: the strongest signal in the project's own EDA
    parts.append((_clip01((1.2 - min(f["vol_ratio_5"], 3.0)) / 1.2), w.get("low_intraday_volume", 18.0)))
    parts.append((_clip01(0.5 + (0.9 - min(f["vol_ratio_10"], 2.0)) / 0.9), w.get("volume_contraction", 14.0)))
    # no recent down streak
    parts.append((1.0 if f["consec_down"] == 0 else max(0.0, 1.0 - 0.5 * f["consec_down"]),
                  w.get("no_recent_down", 14.0)))
    # pushing into the ceiling but not already locked (a locked day cannot be bought)
    parts.append((1.0 if (f["near_ceiling"] and not f["is_locked"]) else (0.4 if f["is_locked"] else 0.0),
                  w.get("near_ceiling", 12.0)))
    parts.append((_clip01((4.0 - min(f["range_pct"], 8.0)) / 4.0), w.get("range_compression", 10.0)))
    # RSI mid band preferred; the project's two camps disagreed, so keep it mild
    parts.append((_clip01(1.0 - abs(f["rsi_14"] - 55.0) / 45.0), w.get("rsi_mid", 10.0)))
    d = f["dist_from_high_52w"]
    parts.append((_clip01((d - 5.0) / 30.0) if d < 35 else _clip01((60.0 - d) / 25.0),
                  w.get("dist_from_52w_high", 12.0)))
    parts.append((_clip01((f["gap_pct"] + 1.0) / 2.0), w.get("low_reopen", 10.0)))

    total_w = sum(p[1] for p in parts) or 1.0
    return float(sum(p[0] * p[1] for p in parts) / total_w)


# ---------------------------------------------------------------------- ranking
def rank_day_prepared(prepared: Dict[str, Dict[str, Any]], day: str,
                      weights: Dict[str, float], limit_pct: float = 5.0,
                      overrides: Optional[Dict[str, float]] = None
                      ) -> List[Tuple[str, float, Dict[str, float]]]:
    overrides = overrides or {}
    rows: List[Tuple[str, float, Dict[str, float]]] = []
    for sym, p in prepared.items():
        i = p["pos"].get(day)
        if i is None:
            continue
        f = features_from_prep(p, i, float(overrides.get(sym, limit_pct)))
        if f is None:
            continue
        rows.append((sym, score_features(f, weights), f))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows


def rank_day(panel: Dict[str, pd.DataFrame], day: str, weights: Dict[str, float],
             limit_pct: float = 5.0, overrides: Optional[Dict[str, float]] = None,
             prepared: Optional[Dict[str, Dict[str, Any]]] = None):
    prep = prepared or prepare_panel(panel)
    return rank_day_prepared(prep, day, weights, limit_pct, overrides)


# ------------------------------------------------------------------ pick output
@dataclass
class PickRow:
    symbol: str
    score: float
    for_date: str
    rank: int


def generate_picks(cfg, asof: Optional[str] = None, write: bool = True) -> List:
    """Build tomorrow's pick list from the latest complete daily bar."""
    from .engine import Pick

    panel = load_panel(cfg.path("market_data_dir"))
    if not panel:
        raise RuntimeError(f"no daily data in {cfg.path('market_data_dir')}")
    prepared = prepare_panel(panel)
    last_day = max(p["dates"][-1] for p in prepared.values())
    day = asof or last_day
    sig = cfg.section("signals")
    rows = rank_day_prepared(prepared, day, sig.get("weights") or {},
                             float(cfg.get("market.daily_limit_pct", 5.0)),
                             cfg.get("market.limit_pct_overrides") or {})
    top_n = int(sig.get("top_n", 3))
    min_score = float(cfg.get("buy.min_score", 0.6))
    for_date_g = _next_g(day, cfg)

    if write:
        payload = {
            "schema": 2,
            "generated_at": now_tehran().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "rules_v2",
            "mode": "rank_only",
            "data_day_jal": day,
            "for_date_g": for_date_g,
            "total_symbols": len(rows),
            "min_score": min_score,
            "picks": [{"symbol": s, "score": round(sc, 6), "rank": i + 1}
                      for i, (s, sc, _) in enumerate(rows[:top_n])],
            "top": [{"symbol": s, "score": round(sc, 6)} for s, sc, _ in rows[:15]],
            "explain": {s: {k: round(v, 4) for k, v in f.items()
                            if isinstance(v, (int, float))} for s, _, f in rows[:top_n]},
            "note": "rank-only score in [0,1]; not a probability",
        }
        out_dir = cfg.path("picks_dir")
        out_dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        (out_dir / "picks_v2.json").write_text(text, encoding="utf-8")
        (out_dir / "today_picks.json").write_text(text, encoding="utf-8")

    return [Pick(symbol=s, score=sc, for_date=for_date_g, source="rules_v2")
            for s, sc, _ in rows[:top_n] if sc >= min_score][:top_n]


def _next_g(day_jal: str, cfg) -> str:
    cal = TradingCalendar(holidays_path=cfg.path("state_dir") / "holidays.json")
    return cal.next_trading_day(from_jalali(day_jal)).isoformat()
