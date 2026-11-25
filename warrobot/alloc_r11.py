"""Round 11: the same allocation tests, now on the FULL history (1397-1405).

Stocks come from data/daily_full (25 years of raw TSETMC history, converted to
Jalali), so the 1398-1400 gap that invalidated the earlier windows is gone.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jdatetime
import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE))
import fulldata  # noqa: E402

OUT = BASE / "state" / "alloc_r11.json"
HOLD = 20
MA = 200
COST = 0.005


def fund_series(name: str) -> pd.Series:
    d = pd.read_csv(BASE / "data" / "funds" / f"{name}.csv", dtype={"date": str})

    def to_j(g: str) -> str:
        try:
            y, m, dd = (int(x) for x in str(g).split("-"))
            return jdatetime.date.fromgregorian(year=y, month=m, day=dd).strftime("%Y-%m-%d")
        except Exception:
            return ""

    d["j"] = d["date"].map(to_j)
    s = d[d["j"] != ""].set_index("j")["close"].astype(float)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    r = s.pct_change()
    return s[(r.abs() < 0.40) | r.isna()]


def build() -> pd.DataFrame:
    idx = fulldata.equal_weight_index("1397-01-01")
    df = pd.DataFrame(index=idx.index)
    df["px_s"] = idx.values
    df["stock"] = idx.pct_change().fillna(0).values
    for label, fname in (("gold", "طلا"), ("fixed", "اعتماد")):
        df[label] = fund_series(fname).pct_change().reindex(df.index).ffill().fillna(0).values
    df["ma_s"] = df["px_s"].rolling(MA, min_periods=120).mean()
    df["px_g"] = (1 + df["gold"]).cumprod()
    df["ma_g"] = df["px_g"].rolling(MA, min_periods=120).mean()
    return df.dropna(subset=["ma_s"])


def sim(df: pd.DataFrame, rule: str, start: str, end: str) -> dict:
    days = list(df.index)
    eq, curve, prev = 1.0, [], None
    for k in range(0, len(days) - HOLD, HOLD):
        d0, d1 = days[k], days[min(k + HOLD - 1, len(days) - 1)]
        if d0 < start or d1 >= end:
            continue
        win = df.iloc[k:k + HOLD]
        st_up = df["px_s"].iloc[k] > df["ma_s"].iloc[k]
        gd_up = df["px_g"].iloc[k] > df["ma_g"].iloc[k] if not pd.isna(df["ma_g"].iloc[k]) else False
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
    df = build()
    print("sessions:", len(df), "|", df.index.min(), "->", df.index.max())
    res = {}
    for label, (a, b) in {"1398-1405": ("1398-01-01", "1406-01-01"),
                          "1401-1405": ("1401-01-01", "1406-01-01")}.items():
        print(f"\n=== {label} (دادهٔ کامل) ===")
        res[label] = {}
        for rule in ("stocks", "guard"):
            r = sim(df, rule, a, b)
            res[label][rule] = r
            print(f"  {rule:8s} {r['ret']:+10.1f}% · افت {r['dd']:5.1f}% · نسبت {r['ratio']:7.1f}")

    print("\n=== سال‌به‌سال (guard | stocks) ===")
    years = {}
    for y in ("1397", "1398", "1399", "1400", "1401", "1402", "1403", "1404", "1405"):
        a, b = f"{y}-01-01", f"{int(y)+1}-01-01"
        g = sim(df, "guard", a, b)["ret"]
        s = sim(df, "stocks", a, b)["ret"]
        years[y] = {"guard": g, "stocks": s}
        mark = "✔" if g > s else "✘"
        print(f"  {y}: {g:+9.1f}% | {s:+9.1f}%  {mark}")
    wins = sum(1 for y in years if years[y]["guard"] > years[y]["stocks"])
    res["years"] = years
    res["guard_wins"] = wins
    print(f"\nنگهبان در {wins} سال از {len(years)} سال بهتر بود")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
