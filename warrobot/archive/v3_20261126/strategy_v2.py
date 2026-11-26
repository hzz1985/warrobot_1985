"""WarRobot strategy v2 — rule set shared by the live bot and backtest_v2.py.

Switch in config.json:
    "strategy": {"version": "v1"}            -> legacy flow (unchanged, default)
    "strategy": {"version": "v2", "v2": {...}} -> rules below

Everything here is pure (no broker / network) so the backtest and the live code
use exactly the same decisions.

Prices on the Tehran exchange are bounded by a daily limit L around the
reference price (priceYesterday):  floor = ref*(1-L), ceiling = ref*(1+L).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

# --------------------------------------------------------------------------- fees
# SEO tariff 1404-1405 (stocks, TSE main board): buy 0.3712 %, sell 0.88 % incl.
# 0.5 % sale tax -> round trip ~1.25 %. Farabourse buy is 0.3632 %.
BUY_FEE = 0.003712
SELL_FEE = 0.0088


@dataclass
class V2Config:
    # ---- entry ------------------------------------------------------------
    entry: str = "queue"            # queue  : pre-open bid at next ceiling (v1 behaviour)
                                    # open_unlocked: buy only if next session opens below ceiling
    first_lock_only: bool = False   # True = only the FIRST ceiling-lock (tested: did not help)
    min_value_rial: float = 2e10    # min traded value on the signal day (liquidity / queue strength)
    max_picks: int = 6
    # market regime on the equal-weight breadth index (all stocks)
    regime_ma: int = 50             # 0 = off; else equal-weight index close must be > its SMA(regime_ma)
    avoid_floor_lock_days: int = 10  # skip symbols locked at the floor in the last N sessions (0=off)
    # ---- sizing -----------------------------------------------------------
    max_positions: int = 10          # v1 used 3; more slots = less single-name risk
    # ---- exit -------------------------------------------------------------
    tp: float | None = None         # take profit (fraction) or None
    sl_limits: float = 3.0          # catastrophe stop = sl_limits * daily limit (3 x 3 % = 9 %)
    sl_fixed: float | None = None   # if set, overrides sl_limits (v1 used 0.03)
    trail: float | None = None      # trailing stop from the highest close (fraction) or None
    max_hold: int = 15              # sessions (time exit at the close)
    slippage: float = 0.0025        # per market-style fill (open / stop / time exit)

    @classmethod
    def from_config(cls, cfg: dict) -> "V2Config":
        raw = ((cfg or {}).get("strategy") or {}).get("v2") or {}
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)


V1_EQUIVALENT = dict(entry="queue", first_lock_only=False, min_value_rial=3e8, max_picks=6,
                     regime_ma=0, avoid_floor_lock_days=0, max_positions=3, tp=0.25,
                     sl_limits=1.0, sl_fixed=0.03, trail=None, max_hold=15)


def load(path: str | Path) -> tuple[str, V2Config]:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    ver = ((cfg.get("strategy") or {}).get("version") or "v1").lower()
    return ver, V2Config.from_config(cfg)


# ------------------------------------------------------------------- primitives
def ceiling(ref: float, limit: float) -> float:
    return ref * (1 + limit)


def floor_px(ref: float, limit: float) -> float:
    return ref * (1 - limit)


def is_lock_up(ref: float, high: float, close: float, last: float, limit: float,
               band: float = 0.0025) -> bool:
    """Closed locked at the ceiling: last trade == high and final price within band."""
    c = ceiling(ref, limit)
    return last >= high and high >= c * 0.997 and close >= c * (1 - band) * 0.997


def is_lock_down(ref: float, low: float, last: float, limit: float) -> bool:
    return last <= low and low <= floor_px(ref, limit) * 1.003


def pick_candidates(rows: list[dict], cfg: V2Config, regime_ok: bool,
                    held: set[str]) -> list[dict]:
    """rows: one dict per symbol for the signal session with keys
    sym, lock_up, prev_lock_up, value, floor_lock_recent.
    Returns the ranked list (most traded first), at most cfg.max_picks."""
    if cfg.regime_ma and not regime_ok:
        return []
    out = []
    for r in rows:
        if not r["lock_up"] or r["sym"] in held:
            continue
        if cfg.first_lock_only and r.get("prev_lock_up"):
            continue
        if r["value"] < cfg.min_value_rial:
            continue
        if cfg.avoid_floor_lock_days and r.get("floor_lock_recent"):
            continue
        out.append(r)
    out.sort(key=lambda r: -r["value"])
    return out[: cfg.max_picks]


def stop_level(cfg: V2Config, limit: float, peak: float) -> float:
    """Stop as a ratio of entry (1.0 = entry price). peak = highest close ratio."""
    sl = cfg.sl_fixed if cfg.sl_fixed is not None else cfg.sl_limits * limit
    s = 1 - sl
    if cfg.trail:
        s = max(s, peak * (1 - cfg.trail))
    return s


def exit_decision(cfg: V2Config, r_prev: float, peak: float, held: int, bar: dict,
                  limit: float) -> tuple[str | None, float | None]:
    """One session for an open position.

    r_prev : position value ratio at the previous close (1.0 = entry, total return)
    bar    : o, h, l, c, py (raw prices of this session, py = reference price)
    Returns (reason, exit_ratio) or (None, None).  Exit ratio is before fees and
    slippage.  A session locked at the floor all day cannot be sold.
    """
    py = bar["py"]
    k = r_prev / py
    o, h, l, c = bar["o"] or bar["c"], bar["h"], bar["l"], bar["c"]
    if h <= floor_px(py, limit) * 1.003:            # floor-locked all session: unsellable
        return None, None
    s = stop_level(cfg, limit, peak)
    t = (1 + cfg.tp) if cfg.tp else None
    ro, rh, rl = o * k, h * k, l * k
    if ro <= s:
        return "stop_gap", ro
    if t and ro >= t:
        return "tp_gap", ro
    if rl <= s:                                      # conservative: stop before target
        return "stop", s
    if t and rh >= t:
        return "tp", t
    if held + 1 >= cfg.max_hold and not is_lock_down(py, l, c, limit):
        return "time", c * k
    return None, None


# ------------------------------------------------------------------ live wiring
# Small, defensive helpers the existing scripts call.  With "version": "v1" (the
# default) every helper returns exactly the legacy numbers, so nothing changes.
def live_params(config_path: str | Path, limit: float = 0.03) -> dict:
    """Effective exit / sizing numbers for the running bot."""
    try:
        cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    ver = ((cfg.get("strategy") or {}).get("version") or "v1").lower()
    sell = cfg.get("sell") or {}
    if ver not in ("v2", "v3"):                     # v3 = v2 exits + extra entry filters (strategy_v3.py)
        return dict(version="v1", tp_pct=float(sell.get("target_pct", 25.0)),
                    sl_pct=float(sell.get("stop_pct", 3.0)),
                    max_hold_days=int(sell.get("max_hold_days", 15)), trail_pct=None, slots=3)
    v = V2Config.from_config(cfg)
    sl = v.sl_fixed if v.sl_fixed is not None else v.sl_limits * limit
    return dict(version="v2", strategy=ver, tp_pct=(v.tp * 100) if v.tp else None, sl_pct=round(sl * 100, 3),
                max_hold_days=int(v.max_hold), trail_pct=(v.trail * 100) if v.trail else None,
                slots=int(v.max_positions))


def regime_ok_from_daily(daily_dir: str | Path, ma: int, limit: float = 0.03) -> bool:
    """Equal-weight index of every symbol's close/prev-close, close > SMA(ma)."""
    import pandas as pd
    if not ma:
        return True
    rets = []
    for f in Path(daily_dir).glob("*_daily.csv"):
        try:
            d = pd.read_csv(f, usecols=["date", "Close"], dtype={"date": str}).sort_values("date")
        except Exception:
            continue
        if len(d) < ma + 2:
            continue
        r = d.set_index("date")["Close"].astype(float).pct_change().clip(-0.25, 0.25)
        rets.append(r.tail(ma * 3))
    if not rets:
        return True                       # no data -> do not block (logged by caller)
    ew = (1 + pd.concat(rets, axis=1).mean(axis=1).fillna(0)).cumprod()
    return bool(ew.iloc[-1] > ew.rolling(ma).mean().iloc[-1])


def filter_scan(cands, config_path: str | Path, daily_dir: str | Path, limit: float = 0.03):
    """Apply the v2 entry filters to distbot.scan_nightly() output (DataFrame with
    symbol, turnover, ...).  v1 -> returned unchanged."""
    import pandas as pd
    try:
        cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception:
        return cands
    if ((cfg.get("strategy") or {}).get("version") or "v1").lower() != "v2" or cands is None or not len(cands):
        return cands
    return apply_v2_filters(cands, V2Config.from_config(cfg), daily_dir, limit)


def apply_v2_filters(cands, v: "V2Config", daily_dir: str | Path, limit: float = 0.03):
    """The v2 entry filters themselves (regime, value, floor-lock, first-lock), ranked by turnover.
    Shared by filter_scan (v2) and strategy_v3.filter_scan."""
    import pandas as pd
    if cands is None or not len(cands):
        return cands
    if v.regime_ma and not regime_ok_from_daily(daily_dir, v.regime_ma, limit):
        return cands.iloc[0:0]
    keep = cands["turnover"].astype(float) >= v.min_value_rial
    if v.avoid_floor_lock_days or v.first_lock_only:
        flags = []
        for sym in cands["symbol"]:
            fl, prev_up = False, False
            try:
                d = pd.read_csv(Path(daily_dir) / f"{sym}_daily.csv", dtype={"date": str}).sort_values("date")
                c, lo = d["Close"].astype(float), d["Low"].astype(float)
                pc = c.shift(1)
                dn = (lo <= pc * (1 - limit) * 1.003) & (c <= lo * 1.001)
                fl = bool(dn.tail(v.avoid_floor_lock_days).any()) if v.avoid_floor_lock_days else False
                up = c >= pc * (1 + limit) * (1 - 0.0025) * 0.999
                prev_up = bool(up.iloc[-2]) if len(up) > 2 else False
            except Exception:
                pass
            flags.append(not fl and not (v.first_lock_only and prev_up))
        keep &= pd.Series(flags, index=cands.index)
    return cands[keep].sort_values("turnover", ascending=False).head(v.max_picks)
