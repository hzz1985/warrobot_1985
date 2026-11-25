"""Walk-forward test: can a simple stock/gold rotation beat just holding gold?

Train window  : 1396-1402 (chooses the lookback)
Test window   : 1403-1405 (only the chosen rule is evaluated)

Rules compared on the test window: the rotation, always-stocks, always-gold and
a fixed 50/50 mix. A rule only counts as an improvement if it beats the best
static alternative on the test window, not on the whole sample.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
D3Y = BASE.parent / "Tse2" / "orderflow" / "data" / "daily3y"
MACRO = BASE / "data" / "macro"
OUT = BASE / "state" / "alloc_wf.json"

LOOKBACKS = (10, 20, 40, 60)
HOLD = 20
TRAIN_END = "1403-01-01"


def load() -> pd.DataFrame:
    rets = []
    for f in D3Y.glob("*.csv"):
        try:
            d = pd.read_csv(f, dtype={"date": str})
        except Exception:
            continue
        if len(d) < 250:
            continue
        s = d.set_index("date")["Close"].astype(float).pct_change()
        rets.append(s.rename(f.stem))
    stock = pd.concat(rets, axis=1).mean(axis=1)

    def macro(name: str) -> pd.Series:
        d = pd.read_csv(MACRO / f"{name}.csv", dtype={"date": str})
        j = d["jalali"].str.replace("/", "-", regex=False)
        s = pd.Series(d["close"].astype(float).values, index=j.values)
        return s[~s.index.duplicated(keep="last")].sort_index()

    days = stock.index
    df = pd.DataFrame(index=days)
    df["stock"] = stock.fillna(0)
    df["gold"] = macro("gold18").pct_change().reindex(days).ffill().fillna(0)
    return df


def run(df: pd.DataFrame, lookback: int, start: str, end: str) -> tuple[float, list[str], list[float]]:
    days = list(df.index)
    eq = 1.0
    picks, curve = [], []
    for k in range(max(60, lookback), len(days) - HOLD, HOLD):
        d0, d1 = days[k], days[min(k + HOLD - 1, len(days) - 1)]
        if str(d0) < start or str(d1) >= end:
            continue
        hist = df.iloc[max(0, k - lookback):k]
        trail = {a: float((1 + hist[a]).prod() - 1) for a in ("stock", "gold")}
        choice = max(trail, key=trail.get)
        r = float((1 + df.iloc[k:k + HOLD][choice]).prod() - 1)
        eq *= 1 + r
        picks.append(choice)
        curve.append(eq)
    return eq - 1, picks, curve


def dd(curve: list[float]) -> float:
    if not curve:
        return 0.0
    s = pd.Series(curve)
    return float((1 - s / s.cummax()).max() * 100)


def year_range(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    m = (df.index.astype(str) >= start) & (df.index.astype(str) < end)
    return df[m]


def main() -> int:
    df = load()
    df.index = df.index.astype(str)
    print("sessions:", len(df), "|", df.index.min(), "->", df.index.max())

    # 1) choose the lookback on the TRAIN window
    train = {}
    for lb in LOOKBACKS:
        r, picks, curve = run(df, lb, "1396-01-01", TRAIN_END)
        train[lb] = {"ret": round(r * 100, 1), "dd": round(dd(curve), 1),
                     "gold_share": round(100 * picks.count("gold") / max(len(picks), 1), 0)}
        print(f"  train lb={lb:3d}: {train[lb]['ret']:+8.1f}% · dd {train[lb]['dd']:5.1f}% · gold {train[lb]['gold_share']:.0f}%")
    best_lb = max(train, key=lambda k: train[k]["ret"])
    print("chosen lookback:", best_lb)

    # 2) test window
    te = year_range(df, TRAIN_END, "1406-01-01")
    rot, picks, curve = run(df, best_lb, TRAIN_END, "1406-01-01")
    statics = {
        "always_stocks": float((1 + te["stock"]).prod() - 1),
        "always_gold": float((1 + te["gold"]).prod() - 1),
        "mix_50_50": float((1 + 0.5 * te["stock"] + 0.5 * te["gold"]).prod() - 1),
    }
    print("\nTEST window 1403-1405:")
    print(f"  rotation(lb={best_lb}): {rot*100:+8.1f}% · dd {dd(curve):5.1f}% · "
          f"gold {100*picks.count('gold')/max(len(picks),1):.0f}%")
    for k, v in statics.items():
        print(f"  {k:14s}: {v*100:+8.1f}%")

    # per year on the test window
    years = {}
    for y in ("1403", "1404", "1405"):
        sub_r, _, _ = run(df, best_lb, f"{y}-01-01", f"{int(y)+1}-01-01")
        sub = year_range(df, f"{y}-01-01", f"{int(y)+1}-01-01")
        years[y] = {"rotation": round(sub_r * 100, 1),
                    "stocks": round(float((1 + sub["stock"]).prod() - 1) * 100, 1),
                    "gold": round(float((1 + sub["gold"]).prod() - 1) * 100, 1)}
        print(f"  {y}: چرخش {years[y]['rotation']:+7.1f}% | سهم {years[y]['stocks']:+7.1f}% | طلا {years[y]['gold']:+7.1f}%")

    beaten = rot > max(statics.values())
    res = {"train": train, "chosen_lookback": best_lb,
           "test_rotation": round(rot * 100, 1), "test_dd": round(dd(curve), 1),
           "test_statics": {k: round(v * 100, 1) for k, v in statics.items()},
           "test_years": years,
           "beats_best_static": bool(beaten)}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nنتیجه: چرخش از بهترین گزینهٔ ثابت بهتر بود؟", "بله" if beaten else "خیر")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
