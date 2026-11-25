"""Round 9: longer window + a USD filter on top of the trend guard.

Variants:
    guard          : the round-6/7 rule (index MA200, gold fund MA200)
    guard_usd      : gold only counts as an uptrend when the USD rate is also
                     above its own MA200 (gold in rial is a dollar trade)
Window: 1398-1405 (the whole span where the funds exist), plus per-year detail.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE))
from alloc_funds import load as load_funds  # noqa: E402

OUT = BASE / "state" / "alloc_r9.json"
HOLD = 20
MA = 200
COST = 0.005


def add_usd(df: pd.DataFrame) -> pd.DataFrame:
    d = pd.read_csv(BASE / "data" / "macro" / "usd.csv", dtype={"date": str})
    j = d["jalali"].str.replace("/", "-", regex=False)
    s = pd.Series(d["close"].astype(float).values, index=j.values)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s = s[s.pct_change().abs() < 0.4]
    df = df.copy()
    df["usd"] = s.pct_change().reindex(df.index).ffill().fillna(0).values
    df["px_u"] = (1 + df["usd"]).cumprod()
    df["ma_u"] = df["px_u"].rolling(MA, min_periods=120).mean()
    return df


def sim(df: pd.DataFrame, rule: str, start: str, end: str) -> dict:
    days = list(df.index)
    eq, curve, prev = 1.0, [], None
    for k in range(MA, len(days) - HOLD, HOLD):
        d0, d1 = days[k], days[min(k + HOLD - 1, len(days) - 1)]
        if d0 < start or d1 >= end:
            continue
        win = df.iloc[k:k + HOLD]
        st_up = df["px_s"].iloc[k - 1] > df["ma_s"].iloc[k - 1]
        gd_up = df["px_g"].iloc[k - 1] > df["ma_g"].iloc[k - 1]
        usd_up = df["px_u"].iloc[k - 1] > df["ma_u"].iloc[k - 1] if "px_u" in df else True
        if rule == "stocks":
            r = float((1 + win["stock"]).prod() - 1)
            alloc = "stock"
        elif rule == "guard":
            if st_up:
                r = float((1 + 0.5 * win["stock"] + 0.5 * win["gold"]).prod() - 1)
                alloc = "half"
            elif gd_up:
                r = float((1 + win["gold"]).prod() - 1)
                alloc = "gold"
            else:
                r = float((1 + win["fixed"]).prod() - 1)
                alloc = "fixed"
        elif rule == "guard_usd":
            gd = gd_up and usd_up
            if st_up:
                r = float((1 + 0.5 * win["stock"] + 0.5 * win["gold"]).prod() - 1)
                alloc = "half"
            elif gd:
                r = float((1 + win["gold"]).prod() - 1)
                alloc = "gold"
            else:
                r = float((1 + win["fixed"]).prod() - 1)
                alloc = "fixed"
        else:
            r, alloc = 0.0, "none"
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
    df = add_usd(load_funds())
    print("sessions:", len(df), "|", df.index.min(), "->", df.index.max())
    res = {}
    for window, (a, b) in {"1398-1405": ("1398-01-01", "1406-01-01"),
                           "1401-1405": ("1401-01-01", "1406-01-01")}.items():
        print(f"\n=== {window} ===")
        res[window] = {}
        for rule in ("stocks", "guard", "guard_usd"):
            r = sim(df, rule, a, b)
            res[window][rule] = r
            print(f"  {rule:10s} {r['ret']:+10.1f}% · افت {r['dd']:5.1f}% · نسبت {r['ratio']:7.1f}")

    print("\n=== سال‌به‌سال (guard | guard_usd | stocks) ===")
    years = {}
    for y in ("1398", "1399", "1400", "1401", "1402", "1403", "1404", "1405"):
        a, b = f"{y}-01-01", f"{int(y)+1}-01-01"
        g1 = sim(df, "guard", a, b)["ret"]
        g2 = sim(df, "guard_usd", a, b)["ret"]
        st = sim(df, "stocks", a, b)["ret"]
        years[y] = {"guard": g1, "guard_usd": g2, "stocks": st}
        print(f"  {y}: {g1:+8.1f}% | {g2:+8.1f}% | {st:+8.1f}%")
    wins = sum(1 for y in years if years[y]["guard_usd"] > years[y]["stocks"])
    res["years"] = years
    res["guard_usd_beats_stocks"] = wins
    print(f"\nguard_usd بهتر از سهم‌تنها در {wins} سال از {len(years)} سال")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
