"""Real backtest of the price/volume part of the Type-A pre-disclosure strategy.

Data (all real, local TSETMC daily files): 790 symbols, 1404-01-05 .. 1405-08-20.

Entry rule (the two conditions that can be measured from TSETMC alone):
    vol_ratio = today's volume / 20-session average  > 1.8
    close >= 0.90 * max(high, last 60 sessions)      (within 10% of a 3-month high)
Entry at the next session's open (no same-bar execution).

Exit (in order): +8% target, -4% stop, or 10 sessions elapsed.

Because the codal Type-A labels are still being collected (rate limited), this
run measures the *unconditional* performance of the rule and the signal
frequency; the conditional win-rate on labelled Type-A events is added later by
the same script when the event file is complete.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
DAILY = BASE.parent / "TeseRobot" / "data" / "daily"
OUT = BASE / "research" / "signal_backtest_result.json"

VOL_RATIO = 1.8
HIGH_PROX = 0.90
TARGET = 0.08
STOP = -0.04
MAX_HOLD = 10
RF = 0.23


def load_one(fp: Path) -> pd.DataFrame | None:
    try:
        d = pd.read_csv(fp)
    except Exception:
        return None
    if len(d) < 90 or "Close" not in d:
        return None
    d = d.dropna(subset=["Close", "Volume"])
    d = d[d["Close"] > 0]
    if len(d) < 90:
        return None
    d["vol_ma20"] = d["Volume"].rolling(20).mean()
    d["high60"] = d["High"].rolling(60).max()
    d["vol_ratio"] = d["Volume"] / d["vol_ma20"]
    d["prox_high"] = d["Close"] / d["high60"]
    return d.reset_index(drop=True)


def simulate(d: pd.DataFrame) -> list[dict]:
    trades = []
    i = 0
    n = len(d)
    while i < n - 60:
        row = d.iloc[i]
        if not (row["vol_ratio"] > VOL_RATIO and row["prox_high"] >= HIGH_PROX):
            i += 1
            continue
        entry_i = i + 1
        if entry_i >= n:
            break
        entry = float(d.iloc[entry_i]["Open"]) or float(d.iloc[entry_i]["Close"])
        if entry <= 0:
            i += 1
            continue
        exit_price, exit_i, why = None, None, None
        for k in range(entry_i, min(entry_i + MAX_HOLD, n)):
            hi = float(d.iloc[k]["High"])
            lo = float(d.iloc[k]["Low"])
            # conservative: stop is checked before the target inside the same bar
            if lo / entry - 1 <= STOP:
                exit_price, exit_i, why = entry * (1 + STOP), k, "stop"
                break
            if hi / entry - 1 >= TARGET:
                exit_price, exit_i, why = entry * (1 + TARGET), k, "target"
                break
        if exit_price is None:
            exit_i = min(entry_i + MAX_HOLD - 1, n - 1)
            exit_price, why = float(d.iloc[exit_i]["Close"]), "time"
        # precursor statistic: does +3% appear within the next 3 sessions?
        p3 = False
        for k in range(entry_i, min(entry_i + 3, n)):
            if float(d.iloc[k]["High"]) / entry - 1 >= 0.03:
                p3 = True
                break
        fees = 0.005 * 2
        ret = exit_price / entry - 1 - fees
        trades.append({"entry_date": str(d.iloc[entry_i]["date"]),
                       "exit_date": str(d.iloc[exit_i]["date"]),
                       "ret": ret, "why": why,
                       "p3_in_3d": p3,
                       "hold": exit_i - entry_i + 1})
        i = exit_i + 1          # no overlapping positions in the same symbol
    return trades


def main() -> int:
    files = sorted(DAILY.glob("*_daily.csv"))
    all_trades = []
    per_symbol_signals = {}
    for fp in files:
        d = load_one(fp)
        if d is None:
            continue
        tr = simulate(d)
        per_symbol_signals[fp.stem.replace("_daily", "")] = len(tr)
        all_trades.extend(tr)

    if not all_trades:
        print("no trades")
        return 1

    rets = [t["ret"] for t in all_trades]
    wins = [r for r in rets if r > 0]
    # equity: one unit per trade, sequential, equal weight
    eq = [1.0]
    for r in rets:
        eq.append(eq[-1] * (1 + r / 3))   # max 3 concurrent slots
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    # monthly aggregation gives a defensible annualised Sharpe
    from collections import defaultdict
    monthly = defaultdict(list)
    for t in all_trades:
        monthly[str(t["exit_date"])[:7]].append(t["ret"])
    m_rets = [statistics.fmean(v) / 3 for _, v in sorted(monthly.items())]  # 3 slots
    rf_m = RF / 12
    if len(m_rets) > 2 and statistics.pstdev(m_rets) > 0:
        sharpe = (statistics.fmean(m_rets) - rf_m) / statistics.pstdev(m_rets) * math.sqrt(12)
    else:
        sharpe = 0.0

    by_year = {}
    for t in all_trades:
        y = t["entry_date"][:4]
        by_year.setdefault(y, []).append(t)

    result = {
        "data": {"symbols": len(files), "trades": len(all_trades),
                 "period": "1404-01-05 .. 1405-08-20"},
        "rules": {"vol_ratio": VOL_RATIO, "high_prox": HIGH_PROX, "target": TARGET,
                  "stop": STOP, "max_hold": MAX_HOLD, "fees_roundtrip": 0.01},
        "metrics": {
            "win_rate_pct": round(100 * len(wins) / len(rets), 2),
            "avg_return_pct": round(100 * statistics.fmean(rets), 3),
            "median_return_pct": round(100 * statistics.median(rets), 3),
            "avg_hold_days": round(statistics.fmean([t["hold"] for t in all_trades]), 2),
            "max_drawdown_pct": round(100 * mdd, 2),
            "sharpe_annualised": round(sharpe, 2),
            "signals_per_year": round(len(all_trades) / 1.6, 1),
            "pct_up3_in_3days": round(100 * sum(1 for t in all_trades if t.get("p3_in_3d")) / len(all_trades), 2),
            "target_exits": sum(1 for t in all_trades if t["why"] == "target"),
            "stop_exits": sum(1 for t in all_trades if t["why"] == "stop"),
            "time_exits": sum(1 for t in all_trades if t["why"] == "time"),
        },
        "by_year": {y: {"trades": len(v),
                        "win_pct": round(100 * sum(1 for t in v if t["ret"] > 0) / len(v), 2),
                        "avg_ret_pct": round(100 * statistics.fmean([t["ret"] for t in v]), 3)}
                    for y, v in sorted(by_year.items())},
        "most_active_symbols": sorted(per_symbol_signals.items(),
                                      key=lambda kv: -kv[1])[:10],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=1))
    print("by_year:", json.dumps(result["by_year"], ensure_ascii=False))
    print("saved:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
