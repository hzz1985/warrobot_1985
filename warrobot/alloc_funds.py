"""Allocation test with REAL fund prices (no proxies).

Assets: stocks (equal-weight of the local daily files), gold fund (طلا),
fixed-income fund (اعتماد), leveraged fund (اهرم).
Window: the period where all four exist (1401 onwards).

Rules, rebalanced every 20 sessions, 0.5% cost per change:
    stocks / gold_fund / fixed / lev          : 100% in one asset
    half_stock_gold                            : 50/50 stocks and gold fund
    trend_guard                                : 50/50 while the index is above
                                                 MA200, else gold fund if that is
                                                 trending up, else fixed income
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE))
OUT = BASE / "state" / "alloc_funds.json"
HOLD = 20
COST = 0.005
MA = 200


def load() -> pd.DataFrame:
    D3Y = BASE.parent / "Tse2" / "orderflow" / "data" / "daily3y"
    rets = []
    for f in D3Y.glob("*.csv"):
        try:
            d = pd.read_csv(f, dtype={"date": str})
        except Exception:
            continue
        if len(d) < 250:
            continue
        rets.append(d.set_index("date")["Close"].astype(float).pct_change().rename(f.stem))
    stock = pd.concat(rets, axis=1).mean(axis=1)

    import jdatetime

    def fund(name: str) -> pd.Series:
        d = pd.read_csv(BASE / "data" / "funds" / f"{name}.csv", dtype={"date": str})
        def to_j(g: str) -> str:
            try:
                y, m, dd = (int(x) for x in str(g).split("-"))
                return jdatetime.date.fromgregorian(year=y, month=m, day=dd).strftime("%Y-%m-%d")
            except Exception:
                return ""
        d["jdate"] = d["date"].map(to_j)
        d = d[d["jdate"] != ""]
        s = d.set_index("jdate")["close"].astype(float)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        # the feed occasionally returns an unadjusted split (e.g. a 10x jump);
        # treat those as missing rather than as a -90% day
        r = s.pct_change()
        s = s[(r.abs() < 0.40) | r.isna()]
        return s

    days = pd.Index(stock.index).astype(str)
    df = pd.DataFrame(index=days)
    df["stock"] = stock.reindex(days).fillna(0).values
    for label, file in (("gold", "طلا"), ("fixed", "اعتماد"), ("lev", "اهرم")):
        df[label] = fund(file).pct_change().reindex(days).ffill().fillna(0).values
    df["px_s"] = (1 + df["stock"]).cumprod()
    df["ma_s"] = df["px_s"].rolling(MA, min_periods=120).mean()
    df["px_g"] = (1 + df["gold"]).cumprod()
    df["ma_g"] = df["px_g"].rolling(MA, min_periods=120).mean()
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

        if rule in ("stocks", "gold", "fixed", "lev"):
            col = "stock" if rule == "stocks" else rule
            r = float((1 + win[col]).prod() - 1)
        elif rule == "half_stock_gold":
            r = float((1 + 0.5 * win["stock"] + 0.5 * win["gold"]).prod() - 1)
        elif rule == "trend_guard":
            if st_up:
                r = float((1 + 0.5 * win["stock"] + 0.5 * win["gold"]).prod() - 1)
            elif gd_up:
                r = float((1 + win["gold"]).prod() - 1)
            else:
                r = float((1 + win["fixed"]).prod() - 1)
        else:
            r = 0.0
        if prev is not None and prev != rule:
            r -= COST if rule in ("half_stock_gold", "trend_guard") else 0.0
        prev = rule
        eq *= 1 + r
        curve.append(eq)
    s = pd.Series(curve) if curve else pd.Series([1.0])
    dd = float((1 - s / s.cummax()).max() * 100) if len(s) > 1 else 0.0
    return {"ret": round((eq - 1) * 100, 1), "dd": round(dd, 1),
            "ratio": round((eq - 1) * 100 / max(dd, 0.1), 1)}


def main() -> int:
    df = load()
    print("sessions:", len(df), "|", df.index.min(), "->", df.index.max())
    res = {}
    for window, (a, b) in {"1401-1405": ("1401-01-01", "1406-01-01"),
                           "1403-1405": ("1403-01-01", "1406-01-01")}.items():
        print(f"\n=== {window} (صندوق‌های واقعی) ===")
        res[window] = {}
        for rule in ("stocks", "gold", "fixed", "lev", "half_stock_gold", "trend_guard"):
            r = sim(df, rule, a, b)
            res[window][rule] = r
            print(f"  {rule:16s} {r['ret']:+9.1f}% · افت {r['dd']:5.1f}% · نسبت {r['ratio']:7.1f}")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
