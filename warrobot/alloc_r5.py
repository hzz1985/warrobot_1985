"""Round 5: adaptive allocation that only moves into gold when GOLD itself is
in an uptrend, so a gold bear market cannot quietly become the whole portfolio.

Rules (rebalance every 20 sessions, signals from the close before the period):
    base            : 100% stocks
    swap_gold       : stocks, but if index < MA200(index) AND gold > MA200(gold)
                      -> 100% gold; otherwise stocks
    swap_cash       : stocks, but if index < MA200(index) -> 100% cash (25%/yr)
    half_guard      : 50/50 stocks/gold while index is above MA200, otherwise
                      gold only if gold is also in an uptrend, else cash

Stress: gold returns forced flat / -10% per year, stocks untouched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE))
from alloc_r3 import load  # noqa: E402

OUT = BASE / "state" / "alloc_r5.json"
HOLD = 20
MA = 200
COST = 0.005
CASH = 0.25


def prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["px_s"] = (1 + df["stock"]).cumprod()
    df["ma_s"] = df["px_s"].rolling(MA, min_periods=120).mean()
    df["px_g"] = (1 + df["gold"]).cumprod()
    df["ma_g"] = df["px_g"].rolling(MA, min_periods=120).mean()
    return df


def sim(df: pd.DataFrame, rule: str, start: str, end: str, gold_mode: str = "real") -> dict:
    dc = (1 + CASH) ** (1 / 252) - 1
    days = list(df.index)
    eq, curve, prev = 1.0, [], None
    for k in range(MA, len(days) - HOLD, HOLD):
        d0, d1 = days[k], days[min(k + HOLD - 1, len(days) - 1)]
        if d0 < start or d1 >= end:
            continue
        win = df.iloc[k:k + HOLD].copy()
        if gold_mode == "flat":
            win["gold"] = 0.0
        elif gold_mode == "bear":
            win["gold"] = (1 - 0.10) ** (1 / 252) - 1
        st_up = df["px_s"].iloc[k - 1] > df["ma_s"].iloc[k - 1]
        gd_up = df["px_g"].iloc[k - 1] > df["ma_g"].iloc[k - 1]

        if rule == "base":
            alloc = "stock"
        elif rule == "swap_gold":
            alloc = "stock" if st_up else ("gold" if gd_up else "cash")
        elif rule == "swap_cash":
            alloc = "stock" if st_up else "cash"
        elif rule == "half_guard":
            if st_up:
                alloc = "half"
            else:
                alloc = "gold" if gd_up else "cash"
        else:
            alloc = "stock"

        if alloc == "stock":
            r = float((1 + win["stock"]).prod() - 1)
        elif alloc == "gold":
            r = float((1 + win["gold"]).prod() - 1)
        elif alloc == "cash":
            r = float((1 + pd.Series([dc] * len(win))).prod() - 1)
        else:
            r = float((1 + 0.5 * win["stock"] + 0.5 * win["gold"]).prod() - 1)

        if prev is not None and prev != alloc:
            r -= COST
        prev = alloc
        eq *= 1 + r
        curve.append(eq)
    s = pd.Series(curve) if curve else pd.Series([1.0])
    dd = float((1 - s / s.cummax()).max() * 100) if len(s) > 1 else 0.0
    return {"ret": round((eq - 1) * 100, 1), "dd": round(dd, 1),
            "ratio": round((eq - 1) * 100 / max(dd, 0.1), 1)}


def main() -> int:
    df = prep(load())
    df.index = df.index.astype(str)
    res = {}
    for window, (a, b) in {"1401-1405": ("1401-01-01", "1406-01-01"),
                           "1403-1405": ("1403-01-01", "1406-01-01")}.items():
        print(f"\n=== {window} ===")
        res[window] = {}
        for rule in ("base", "swap_gold", "swap_cash", "half_guard"):
            r = sim(df, rule, a, b)
            res[window][rule] = r
            print(f"  {rule:12s} {r['ret']:+9.1f}% · افت {r['dd']:5.1f}% · نسبت {r['ratio']:7.1f}")

    print("\n=== سناریوهای طلا (۱۴۰۳–۱۴۰۵) ===")
    for rule in ("swap_gold", "half_guard"):
        for mode, label in (("real", "طلا واقعی"), ("flat", "طلا صفر"), ("bear", "طلا −۱۰٪")):
            r = sim(df, rule, "1403-01-01", "1406-01-01", gold_mode=mode)
            res[f"{rule}_{mode}"] = r
            print(f"  {rule:12s} {label:12s}: {r['ret']:+9.1f}% · افت {r['dd']:5.1f}% · نسبت {r['ratio']:7.1f}")
    base = sim(df, "base", "1403-01-01", "1406-01-01")["ret"]
    print(f"  مرجع سهم‌تنها: {base:+.1f}%")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
