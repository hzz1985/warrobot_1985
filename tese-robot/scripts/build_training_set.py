# -*- coding: utf-8 -*-
"""Build the durable buy-queue training set.

Target (what the user actually wants to buy):
    y3 = 1  iff the symbol locks at the ceiling tomorrow and stays locked
            for at least 3 consecutive sessions (a durable buy-queue).
We also keep the raw run length `q_days` so precision can be reported for
3/5/7-day durability instead of a single threshold.

Features are the union of:
  * daily OHLCV features (full history, computed at the close of day T);
  * queue-state features (was locked today, run history, distance to ceiling);
  * intraday microstructure from `intraday_features_3months.csv`;
  * Burst features (BFE) from `burst_feats_3m.csv`.

Everything is known at the close of T, so the model predicts T+1 without
look-ahead. Output: data/train/queue_train.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tsebot import config as config_mod  # noqa: E402
from tsebot.signals import load_panel, prepare_panel, features_from_prep  # noqa: E402

INTRADAY_COLS = {
    "lock_time_min": "intr_lock_time_min",
    "reopen_count": "intr_reopen_count",
    "pre_lock_vol_M": "intr_pre_lock_vol_M",
    "post_lock_vol_M": "intr_post_lock_vol_M",
    "total_vol_M": "intr_total_vol_M",
    "first_hour_vol_M": "intr_first_hour_vol_M",
    "last_30min_vol_M": "intr_last_30min_vol_M",
    "first_5min_vol_M": "intr_first_5min_vol_M",
    "big_trades_ratio": "intr_big_trades_ratio",
    "avg_trade_size": "intr_avg_trade_size",
    "max_trade_size": "intr_max_trade_size",
    "num_trades": "intr_num_trades",
    "first_trade_min": "intr_first_trade_min",
}
BFE_COLS = ["flk", "flc", "fl60", "flk60", "hs30", "hs60", "pv30",
            "mhi", "nb1", "nb2", "mxr", "tr", "volM"]


def fix_mojibake(s: str) -> str:
    """burst_feats_3m.csv was written with a broken encoding for symbol names."""
    if not isinstance(s, str):
        return str(s)
    for enc in ("cp1252", "latin-1"):
        try:
            fixed = s.encode(enc).decode("utf-8")
            if any("\u0600" <= ch <= "\u06FF" for ch in fixed):
                return fixed
        except Exception:
            continue
    return s


def queue_labels(df: pd.DataFrame, limit_pct: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Return (locked_today, run_length_starting_today) per row."""
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    prev = np.concatenate(([np.nan], c[:-1]))
    locked = (h == l) & (c > prev)
    locked = np.where(np.isnan(prev), False, locked)
    n = len(df)
    run = np.zeros(n, dtype=int)
    i = n - 1
    while i >= 0:
        if locked[i]:
            j = i
            while j + 1 < n and locked[j + 1]:
                j += 1
            run[i:j + 1] = j - i + 1
            i = i - 1
        else:
            i -= 1
    return locked, run


def main() -> int:
    cfg = config_mod.load()
    panel = load_panel(cfg.path("market_data_dir"))
    prepared = prepare_panel(panel)
    print(f"symbols={len(panel)}")

    # ---------------------------------------------------------- intraday + BFE
    intr_path = Path(r"C:\TSE_Strategy\data\intraday_features_3months.csv")
    bfe_path = Path(r"C:\Users\Administrator\Desktop\robot_work\burst_feats_3m.csv")
    intr = pd.read_csv(intr_path, low_memory=False)
    intr["symbol"] = intr["symbol"].astype(str).str.strip()
    intr["date"] = intr["date_jalali"].astype(str).str.strip()
    keep = ["symbol", "date"] + [c for c in INTRADAY_COLS if c in intr.columns]
    intr = intr[keep].rename(columns={k: v for k, v in INTRADAY_COLS.items() if k in intr.columns})
    intr = intr.drop_duplicates(subset=["symbol", "date"])
    print(f"intraday rows={len(intr)} dates={intr['date'].nunique()}")

    bfe = pd.read_csv(bfe_path, low_memory=False)
    bfe["symbol"] = bfe["symbol"].map(fix_mojibake)
    bfe["date"] = bfe["date"].astype(str)
    bfe = bfe[["symbol", "date"] + [c for c in BFE_COLS if c in bfe.columns]]
    bfe = bfe.drop_duplicates(subset=["symbol", "date"])
    print(f"bfe rows={len(bfe)} dates={bfe['date'].nunique()}")

    # ------------------------------------------------------------- row builder
    rows = []
    for sym, p in prepared.items():
        df = panel[sym]
        dates = p["dates"]
        locked, run = queue_labels(df)
        # rolling queue history
        lock_i = locked.astype(int)
        csum = np.concatenate(([0], np.cumsum(lock_i)))
        for i in range(40, len(dates) - 1):
            day = dates[i]
            f = features_from_prep(p, i, 5.0)
            if f is None:
                continue
            # queue state
            last10 = lock_i[max(0, i - 9):i + 1].sum()
            since = -1
            for k in range(i, max(-1, i - 60), -1):
                if lock_i[k] == 0:
                    since = i - k
                    break
            max_run60 = int(run[max(0, i - 59):i + 1].max())
            rows.append({
                "symbol": sym, "date": day,
                "y_locked_today": int(locked[i]),
                "y_locks_last10": int(last10),
                "y_days_since_lock": int(since),
                "y_max_run_60": max_run60,
                "y_dist_to_ceiling_pct": float((f["limit_up"] - f["close"]) / f["close"] * 100.0),
                "y_run_today": int(run[i]),
                # label: queue starting tomorrow
                # only a run that STARTS tomorrow counts: a continuation cannot be bought
                "q_days": int(run[i + 1]) if (locked[i + 1] and not locked[i]) else 0,
                "y3": int(locked[i + 1] and not locked[i] and run[i + 1] >= 3),
                "y5": int(locked[i + 1] and not locked[i] and run[i + 1] >= 5),
                "y7": int(locked[i + 1] and not locked[i] and run[i + 1] >= 7),
                **{k: v for k, v in f.items() if k not in ("limit_up",)},
            })
    base = pd.DataFrame(rows)
    print(f"daily rows={len(base)}")

    merged = base.merge(intr, on=["symbol", "date"], how="left")
    merged = merged.merge(bfe, on=["symbol", "date"], how="left")
    merged["has_intraday"] = merged["intr_total_vol_M"].notna().astype(int)
    merged["has_bfe"] = merged["flk"].notna().astype(int)
    merged["feature_source"] = np.where(merged["has_bfe"] == 1, "full",
                                        np.where(merged["has_intraday"] == 1, "intraday", "daily"))

    out = cfg.path("picks_dir").parent / "train" / "queue_train.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"saved {out}  rows={len(merged)}  cols={merged.shape[1]}")
    print("label rates:",
          {k: round(float(merged[k].mean()) * 100, 2) for k in ("y3", "y5", "y7")})
    print("feature_source:", merged["feature_source"].value_counts().to_dict())
    print("dates:", merged["date"].min(), "..", merged["date"].max(),
          "n=", merged["date"].nunique())
    return 0


if __name__ == "__main__":
    sys.exit(main())

