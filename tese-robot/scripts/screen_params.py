"""Parameter screening: which features actually predict a profitable queue entry?

For every historical signal (symbol, date) we simulate the real trade:
  entry  = next session's open, only if that session is not locked (High==Low)
  exit   = first of  net target T (T% after ~2% round-trip fees)  or  net stop -3%
           within `hold` sessions, using daily High/Low

Then each feature is split into its top quartile vs the rest and we report the
win rate and the average net return for three targets (6 / 9 / 12 percent).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot")
FEE_NET = 2.0          # round-trip broker cost in percent (buy + sell)
STOP_NET = 3.0
HOLD = 10


def load_daily() -> pd.DataFrame:
    rows = []
    for f in (ROOT / "data" / "daily").glob("*_daily.csv"):
        sym = f.name.replace("_daily.csv", "")
        try:
            df = pd.read_csv(f, parse_dates=["date"])
        except Exception:
            continue
        if df.empty:
            continue
        df["symbol"] = sym
        df["locked"] = (df["High"] == df["Low"]) & (df["Close"] > df["Close"].shift(1))
        rows.append(df[["symbol", "date", "Open", "High", "Low", "Close", "locked"]])
    return pd.concat(rows, ignore_index=True)


def outcomes(panel: pd.DataFrame, sig: pd.DataFrame) -> pd.DataFrame:
    """Attach entry price and forward max/min to each signal."""
    panel = panel.sort_values(["symbol", "date"])
    g = panel.groupby("symbol", group_keys=False)
    panel["next_open"] = g["Open"].shift(-1)
    panel["next_date"] = g["date"].shift(-1)
    panel["next_locked"] = g["locked"].shift(-1)
    # forward extremes over the holding window (excluding the entry day)
    fwd_hi = g["High"].transform(lambda s: s.shift(-1).rolling(HOLD, min_periods=1).max())
    fwd_lo = g["Low"].transform(lambda s: s.shift(-1).rolling(HOLD, min_periods=1).min())
    panel["fwd_high"] = fwd_hi
    panel["fwd_low"] = fwd_lo

    cols = ["symbol", "date", "next_open", "next_locked", "fwd_high", "fwd_low"]
    out = sig.merge(panel[cols], on=["symbol", "date"], how="left")
    out = out[out["next_open"].notna() & out["fwd_high"].notna()]
    out = out[~out["next_locked"].fillna(False)]
    entry = out["next_open"].astype(float)
    out["gross_tp6"] = (out["fwd_high"] / entry - 1) * 100
    out["gross_sl"] = (out["fwd_low"] / entry - 1) * 100
    return out


def sim(out: pd.DataFrame, target_net: float) -> pd.Series:
    """1 = target reached before stop, 0 = otherwise; plus realised net return."""
    need_gross = target_net + FEE_NET
    hit = out["gross_tp6"] >= need_gross
    stopped = out["gross_sl"] <= -(STOP_NET - FEE_NET)
    # if both happen we cannot know the order from daily bars: count as stop (conservative)
    win = hit & ~stopped
    return win


def main() -> None:
    sig = pd.read_csv(ROOT / "data" / "train" / "queue_train.csv")
    sig["date"] = pd.to_datetime(sig["date"])
    sig = sig[sig["y3"].notna()].copy()

    panel = load_daily()
    out = outcomes(panel, sig[["symbol", "date", "y3"] + [
        c for c in sig.columns if c not in ("symbol", "date", "y3")
    ]])
    print(f"signals usable: {len(out):,}")

    feats = [
        "intr_lock_time_min", "intr_reopen_count", "intr_pre_lock_vol_M",
        "intr_total_vol_M", "intr_first_hour_vol_M", "intr_last_30min_vol_M",
        "intr_first_5min_vol_M", "intr_big_trades_ratio", "intr_avg_trade_size",
        "intr_max_trade_size", "intr_num_trades", "intr_first_trade_min",
        "flk", "flc", "fl60", "hs30", "hs60", "pv30", "mhi", "nb1", "nb2",
        "mxr", "tr", "volM", "vol_ratio_5", "vol_ratio_10", "vol_ratio_20",
        "rsi_14", "close_pos", "range_pct", "gap_pct", "pos_52w", "consec_down",
        "ret_1d", "ret_5d", "ret_20d", "dist_from_high_52w",
        "y_locks_last10", "y_max_run_60", "y_days_since_lock", "q_days",
    ]
    feats = [f for f in feats if f in out.columns]

    rows = []
    for f in feats:
        s = pd.to_numeric(out[f], errors="coerce")
        if s.notna().sum() < 500 or s.nunique() < 5:
            continue
        thr = s.quantile(0.75)
        m = s >= thr
        rec = {"feature": f, "threshold": round(float(thr), 3), "n_top": int(m.sum())}
        for t in (6.0, 9.0, 12.0):
            win = sim(out.loc[m], t)
            rec[f"win_{int(t)}"] = round(float(win.mean()) * 100, 1)
            rec[f"base_{int(t)}"] = round(float(sim(out, t).mean()) * 100, 1)
        rows.append(rec)

    tbl = pd.DataFrame(rows).sort_values("win_6", ascending=False)
    print("\n=== top-quartile win rate by target (net of 2% fees, stop -3%) ===")
    print(tbl.to_string(index=False))


if __name__ == "__main__":
    main()
