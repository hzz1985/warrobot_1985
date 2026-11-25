# -*- coding: utf-8 -*-
"""TSE Bot v2 — honest backtester.

Differences from every v1 backtest, each one deliberate:

1. FILL MODEL. A position is opened at the next session's open only if that open
   is NOT already at the ceiling. v1 filled 85-89% of its trades on sessions that
   were single-priced (Open=High=Low=Close), i.e. on days you could not buy.
   The optimistic model stays available so the size of that artefact can be
   measured instead of hidden.

2. POSITION LIMIT. At most `max_positions` concurrent trades, capital split
   equally, so returns cannot be compounded over physically overlapping trades
   (v1 turned 148 overlapping trades into +827,650%).

3. COSTS. Commission on both sides plus slippage, always.

4. ONE SCORING FUNCTION. The same `signals.score_features` used live ranks the
   backtest, so the test cannot drift from the product.

5. STATISTICS, NOT A SINGLE NUMBER. Win rate, expectancy, profit factor, max
   drawdown on the slot-based equity curve, and a bootstrap CI for the mean.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import Config
from .signals import load_panel, prepare_panel, rank_day_prepared


@dataclass
class Trade:
    symbol: str
    signal_day: str
    entry_day: str
    exit_day: str
    entry: float
    exit: float
    days: int
    reason: str
    ret_pct: float
    locked_entry: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class BacktestResult:
    start: str = ""
    end: str = ""
    fill_model: str = ""
    signal_days: int = 0
    signals: int = 0
    filled: int = 0
    missed_locked: int = 0
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[Tuple[str, float]] = field(default_factory=list)
    final_equity: float = 1.0
    max_drawdown: float = 0.0
    stats: Dict[str, float] = field(default_factory=dict)
    by_reason: Dict[str, int] = field(default_factory=dict)
    by_year: Dict[str, Dict[str, float]] = field(default_factory=dict)
    benchmark: Dict[str, float] = field(default_factory=dict)

    def to_dict(self):
        d = asdict(self)
        d["trades"] = [t.to_dict() for t in self.trades]
        return d


def _limit_pct(cfg: Config, symbol: str) -> float:
    ov = cfg.get("market.limit_pct_overrides") or {}
    return float(ov.get(symbol, cfg.get("market.daily_limit_pct", 5.0)))


def _simulate_trade(df: pd.DataFrame, entry_idx: int, entry_price: float,
                    cfg: Config) -> Tuple[float, int, str, float, str]:
    bt = cfg.section("backtest")
    target = float(bt.get("target_pct", 10.0))
    stop = float(bt.get("stop_pct", 3.0))
    max_days = int(bt.get("max_hold_days", 10))
    mode = str(bt.get("exit_mode", "barrier"))
    gap_aware = bool(bt.get("gap_aware_stop", True))
    tp = entry_price * (1 + target / 100.0)
    sl = entry_price * (1 - stop / 100.0)
    last_i = min(entry_idx + max_days, len(df) - 1)
    seen_locked = False
    peak = entry_price
    for j in range(entry_idx, last_i + 1):
        row = df.iloc[j]
        low, high, close = float(row["Low"]), float(row["High"]), float(row["Close"])
        opn = float(row["Open"])
        locked = high == low
        if j > entry_idx:
            peak = max(peak, high)
            # ---- stop (first: conservative when both barriers are touched)
            if low <= sl:
                if gap_aware:
                    if locked:
                        # A locked-down session cannot be sold into; exit next open.
                        nj = min(j + 1, len(df) - 1)
                        px = float(df.iloc[nj]["Open"])
                        return ((px / entry_price - 1) * 100, nj - entry_idx, "stop_gap", px,
                                str(df.iloc[nj]["date"]))
                    if opn < sl:
                        return ((opn / entry_price - 1) * 100, j - entry_idx, "stop_gap", opn,
                                str(row["date"]))
                return (-stop, j - entry_idx, "stop", sl, str(row["date"]))
            # ---- queue mode: ride a locked buy-queue, leave on the first unlocked day
            if mode == "queue":
                if locked and close > float(df.iloc[j - 1]["Close"]):
                    seen_locked = True
                    continue
                if seen_locked:
                    return ((close / entry_price - 1) * 100, j - entry_idx, "queue_break", close,
                            str(row["date"]))
                if high >= tp:
                    return (target, j - entry_idx, "target", max(tp, opn), str(row["date"]))
            elif mode == "trail":
                if high >= tp:
                    return (target, j - entry_idx, "target", max(tp, opn), str(row["date"]))
                if peak >= entry_price * 1.05 and close <= peak * 0.96:
                    return ((close / entry_price - 1) * 100, j - entry_idx, "trail", close,
                            str(row["date"]))
            else:
                if high >= tp:
                    return (target, j - entry_idx, "target", max(tp, opn), str(row["date"]))
    row = df.iloc[last_i]
    exit_px = float(row["Close"])
    ret = (exit_px / entry_price - 1.0) * 100.0
    return (ret, last_i - entry_idx, "time", exit_px, str(row["date"]))


def run_backtest(cfg: Config, panel: Optional[Dict[str, pd.DataFrame]] = None,
                 fill_model: Optional[str] = None, top_n: Optional[int] = None,
                 min_score: Optional[float] = None, select: str = "rank",
                 seed: int = 7, flow_features: Optional[Dict] = None,
                 feature_filters: Optional[Dict[str, float]] = None) -> BacktestResult:
    panel = panel or load_panel(cfg.path("market_data_dir"))
    if not panel:
        raise RuntimeError("no market data")
    prepared = prepare_panel(panel)
    bt = cfg.section("backtest")
    fill_model = fill_model or str(bt.get("fill_model", "open_if_not_locked"))
    top_n = int(top_n if top_n is not None else bt.get("top_n", 3))
    min_score = float(min_score if min_score is not None else bt.get("min_score", 0.6))
    sig = cfg.section("signals")
    weights = sig.get("weights") or {}
    max_pos = int(cfg.get("portfolio.max_positions", 3))
    fee_buy = float(cfg.get("fees.buy_pct", 0.6)) / 100.0
    fee_sell = float(cfg.get("fees.sell_pct", 0.6)) / 100.0
    slip = float(bt.get("slippage_pct", 0.3)) / 100.0

    all_days = sorted({d for p in prepared.values() for d in p["dates"]})
    start = str(bt.get("start") or all_days[0])
    end = str(bt.get("end") or all_days[-1])
    days = [d for d in all_days if start <= d <= end]
    if not days:
        raise RuntimeError("empty date range")

    res = BacktestResult(start=days[0], end=days[-1], fill_model=fill_model)
    rng = random.Random(seed)

    # ---------------------------------------------------------- 1) candidates
    candidates: List[Trade] = []
    seen: set = set()
    for day in days:
        rows = rank_day_prepared(prepared, day, weights,
                                 float(cfg.get("market.daily_limit_pct", 5.0)),
                                 cfg.get("market.limit_pct_overrides") or {})
        # Optional parameter screen: attach downloaded flow features and keep only
        # the rows whose feature value clears the threshold. Used by the screening
        # battery; normal runs pass nothing and are unaffected.
        if flow_features or feature_filters:
            kept = []
            for sym, sc, f in rows:
                extra = (flow_features or {}).get((sym, day)) or {}
                merged = dict(f)
                merged.update(extra)
                ok = True
                for name, thr in (feature_filters or {}).items():
                    v = merged.get(name)
                    try:
                        v = float(v)
                    except Exception:
                        ok = False
                        break
                    if v < float(thr):
                        ok = False
                        break
                if ok:
                    kept.append((sym, sc, merged))
            rows = kept
        if select == "random":
            pool = [r for r in rows if r[0] in prepared]
            pick_rows = rng.sample(pool, min(top_n, len(pool))) if pool else []
            cands = [(s, sc) for s, sc, _ in pick_rows]
        else:
            cands = [(s, sc) for s, sc, _ in rows if sc >= min_score][:top_n]
        if cands:
            res.signal_days += 1
        res.signals += len(cands)
        for sym, _sc in cands:
            if (day, sym) in seen:
                continue
            seen.add((day, sym))
            p = prepared[sym]
            idx = [k for k, d in enumerate(p["dates"]) if d > day]
            if not idx:
                continue
            ei = idx[0]
            row = panel[sym].iloc[ei]
            entry_open = float(row["Open"])
            prev_close = float(panel[sym].iloc[ei - 1]["Close"]) if ei > 0 else entry_open
            limit_up = prev_close * (1 + _limit_pct(cfg, sym) / 100.0)
            locked = entry_open >= limit_up * 0.999
            if locked:
                res.missed_locked += 1
                if fill_model != "optimistic":
                    continue
            entry_px = entry_open * (1 + slip)
            ret_pct, days_held, reason, exit_px, exit_day = _simulate_trade(
                panel[sym], ei, entry_px, cfg)
            gross = 1.0 + ret_pct / 100.0
            net = gross * (1 - fee_sell) / (1 + fee_buy)
            candidates.append(Trade(
                symbol=sym, signal_day=day, entry_day=str(row["date"]), exit_day=exit_day,
                entry=entry_px, exit=exit_px, days=days_held, reason=reason,
                ret_pct=(net - 1.0) * 100.0, locked_entry=locked,
            ))

    # ------------------------------------------------ 2) concurrency admission
    candidates.sort(key=lambda t: (t.entry_day, -t.ret_pct))
    accepted: List[Trade] = []
    for t in candidates:
        active = sum(1 for a in accepted
                     if a.entry_day <= t.entry_day <= a.exit_day)
        if active >= max_pos:
            continue
        accepted.append(t)
        res.by_reason[t.reason] = res.by_reason.get(t.reason, 0) + 1
    res.trades = accepted
    res.filled = len(accepted)

    # ------------------------------------------------------- 3) equity & stats
    eq = 1.0
    peak, mdd = 1.0, 0.0
    for t in sorted(accepted, key=lambda x: (x.exit_day, x.entry_day)):
        alloc = eq / max_pos
        eq += alloc * (t.ret_pct / 100.0)
        res.equity_curve.append((t.exit_day, round(eq, 6)))
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1.0)
    res.final_equity = eq
    res.max_drawdown = mdd * 100.0

    rets = np.array([t.ret_pct for t in accepted], dtype=float)
    if len(rets):
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        res.stats = {
            "trades": int(len(rets)),
            "win_rate_pct": round(float((rets > 0).mean() * 100), 2),
            "avg_pct": round(float(rets.mean()), 3),
            "median_pct": round(float(np.median(rets)), 3),
            "std_pct": round(float(rets.std(ddof=1)) if len(rets) > 1 else 0.0, 3),
            "profit_factor": round(float(wins.sum() / -losses.sum()), 3)
            if len(losses) and losses.sum() < 0 else float("inf"),
            "best_pct": round(float(rets.max()), 2),
            "worst_pct": round(float(rets.min()), 2),
            "avg_days": round(float(np.mean([t.days for t in accepted])), 1),
            "total_return_pct": round((eq - 1) * 100, 2),
            "max_drawdown_pct": round(mdd * 100, 2),
            "expectancy_ci95": _bootstrap_ci(rets),
            "fill_rate_pct": round(100.0 * res.filled / max(1, res.signals), 1),
        }
    by_year: Dict[str, Dict[str, float]] = {}
    for t in accepted:
        y = str(t.entry_day)[:4]
        b = by_year.setdefault(y, {"n": 0, "sum": 0.0, "wins": 0})
        b["n"] += 1
        b["sum"] += t.ret_pct
        b["wins"] += 1 if t.ret_pct > 0 else 0
    for y, b in by_year.items():
        b["avg"] = round(b["sum"] / b["n"], 3) if b["n"] else 0.0
        b["win_rate"] = round(b["wins"] * 100.0 / b["n"], 1) if b["n"] else 0.0
    res.by_year = by_year
    res.benchmark = _market_benchmark(panel, days, fee_buy, fee_sell, slip)
    if res.stats:
        res.stats["bench_avg_pct"] = res.benchmark.get("avg_5d_pct", 0.0)
    return res


def _market_benchmark(panel: Dict[str, pd.DataFrame], days: List[str],
                      fee_buy: float, fee_sell: float, slip: float,
                      hold: int = 5) -> Dict[str, float]:
    """Equal-weight buy-and-hold control over the same period.

    If the strategy cannot beat "buy the average stock at the next open and hold
    five sessions", then its edge is market beta, not selection.
    """
    rets: List[float] = []
    for day in days:
        for sym, df in panel.items():
            idx = df.index[df["date"] > day]
            if len(idx) == 0:
                continue
            ei = int(idx[0])
            if ei + hold >= len(df):
                continue
            entry = float(df.iloc[ei]["Open"]) * (1 + slip)
            exit_px = float(df.iloc[ei + hold]["Close"])
            if entry <= 0:
                continue
            gross = exit_px / entry
            net = gross * (1 - fee_sell) / (1 + fee_buy)
            rets.append((net - 1.0) * 100.0)
    if not rets:
        return {}
    arr = np.array(rets, dtype=float)
    return {
        "n": int(len(arr)),
        "avg_5d_pct": round(float(arr.mean()), 3),
        "median_5d_pct": round(float(np.median(arr)), 3),
        "win_rate_pct": round(float((arr > 0).mean() * 100), 1),
    }


def _bootstrap_ci(rets: np.ndarray, n_boot: int = 2000, alpha: float = 0.05) -> List[float]:
    if len(rets) < 5:
        return [float("nan"), float("nan")]
    rng = random.Random(42)
    n = len(rets)
    means = []
    for _ in range(n_boot):
        s = 0.0
        for _ in range(n):
            s += float(rets[rng.randrange(n)])
        means.append(s / n)
    means.sort()
    lo = means[int(alpha / 2 * len(means))]
    hi = means[int((1 - alpha / 2) * len(means)) - 1]
    return [round(float(lo), 3), round(float(hi), 3)]


def format_report(res: BacktestResult) -> str:
    s = res.stats
    lines = [
        "=" * 78,
        "TSE BOT v2 — HONEST BACKTEST",
        "=" * 78,
        f"period            : {res.start} .. {res.end}  (Jalali)",
        f"fill model        : {res.fill_model}",
        f"signal days       : {res.signal_days}",
        f"signals generated : {res.signals}",
        f"entries filled    : {res.filled}",
        f"missed (locked)   : {res.missed_locked}",
        "",
    ]
    if not s:
        lines.append("no trades — nothing to report")
        return "\n".join(lines)
    lines += [
        f"trades            : {s['trades']}",
        f"fill rate         : {s['fill_rate_pct']}%",
        f"win rate          : {s['win_rate_pct']:.1f}%",
        f"avg per trade     : {s['avg_pct']:+.3f}%",
        f"median per trade  : {s['median_pct']:+.3f}%",
        f"std               : {s['std_pct']:.2f}%",
        f"profit factor     : {s['profit_factor']}",
        f"best / worst      : {s['best_pct']:+.2f}% / {s['worst_pct']:+.2f}%",
        f"avg holding days  : {s['avg_days']}",
        f"total return      : {s['total_return_pct']:+.2f}%  (slot-based, compounded)",
        f"max drawdown      : {s['max_drawdown_pct']:.2f}%",
        f"mean 95% CI       : [{s['expectancy_ci95'][0]:+.3f}%, {s['expectancy_ci95'][1]:+.3f}%]",
        "",
    ]
    if res.benchmark:
        lines += [
            "control (equal-weight, next open -> close after 5 sessions):",
            f"  n={res.benchmark.get('n')}  avg={res.benchmark.get('avg_5d_pct'):+.3f}%  "
            f"median={res.benchmark.get('median_5d_pct'):+.3f}%  "
            f"win={res.benchmark.get('win_rate_pct')}%",
            f"  strategy edge over control: {s['avg_pct'] - res.benchmark.get('avg_5d_pct', 0):+.3f}% per trade",
            "",
        ]
    lines += [
        "exit reasons:",
    ]
    for k, v in sorted(res.by_reason.items(), key=lambda x: -x[1]):
        lines.append(f"  {k:<10} {v}")
    if res.by_year:
        lines += ["", "by Jalali year:"]
        for y, b in sorted(res.by_year.items()):
            lines.append(f"  {y}  n={int(b['n']):<4} avg={b['avg']:+.2f}%  win={b['win_rate']:.0f}%")
    lines.append("=" * 78)
    return "\n".join(lines)


