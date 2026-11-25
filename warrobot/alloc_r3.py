"""Round 3: does a trend filter improve the 50/50 mix, and does cash help?

Assets: stocks (equal weight), gold (tgju 18k), cash (fixed-income proxy at a
constant annual rate — the real fund yield, tested at three levels).

Rules (all rebalanced every 20 sessions, signals from data before the period):
    static_50_50      : half stocks, half gold, always
    trend_stock_to_gold: 50/50, but when the stock index is below its 200-day
                        average the stock half moves to gold
    trend_stock_to_cash: same, but the stock half moves to cash
    rotation          : the round-2 momentum rule (baseline)

Reported per rule on 1400-1405 and on the strict test window 1403-1405.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
D3Y = BASE.parent / "Tse2" / "orderflow" / "data" / "daily3y"
MACRO = BASE / "data" / "macro"
OUT = BASE / "state" / "alloc_r3.json"

HOLD = 20
CASH_RATES = (0.0, 0.20, 0.30)


def load() -> pd.DataFrame:
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

    d = pd.read_csv(MACRO / "gold18.csv", dtype={"date": str})
    j = d["jalali"].str.replace("/", "-", regex=False)
    g = pd.Series(d["close"].astype(float).values, index=j.values)
    g = g[~g.index.duplicated(keep="last")].sort_index()

    days = pd.Index(stock.index).astype(str)
    df = pd.DataFrame(index=days)
    df["stock"] = stock.reindex(days).fillna(0).values
    df["gold"] = g.pct_change().reindex(days).ffill().fillna(0).values
    df["ma200"] = (1 + df["stock"]).cumprod().rolling(200, min_periods=120).mean()
    df["px"] = (1 + df["stock"]).cumprod()
    return df


def simulate(df: pd.DataFrame, rule: str, cash_rate: float, start: str, end: str) -> dict:
    daily_cash = (1 + cash_rate) ** (1 / 252) - 1
    days = list(df.index)
    eq, curve = 1.0, []
    for k in range(200, len(days) - HOLD, HOLD):
        d0, d1 = days[k], days[min(k + HOLD - 1, len(days) - 1)]
        if d0 < start or d1 >= end:
            continue
        win = df.iloc[k:k + HOLD]
        trend_up = df["px"].iloc[k - 1] > df["ma200"].iloc[k - 1]
        if rule == "static_50_50":
            r = float((1 + 0.5 * win["stock"] + 0.5 * win["gold"]).prod() - 1)
        elif rule == "trend_stock_to_gold":
            if trend_up:
                r = float((1 + 0.5 * win["stock"] + 0.5 * win["gold"]).prod() - 1)
            else:
                r = float((1 + win["gold"]).prod() - 1)
        elif rule == "trend_stock_to_cash":
            if trend_up:
                r = float((1 + 0.5 * win["stock"] + 0.5 * win["gold"]).prod() - 1)
            else:
                r = float((1 + 0.5 * win["gold"] + 0.5 * daily_cash).prod() - 1)
        elif rule == "always_stocks":
            r = float((1 + win["stock"]).prod() - 1)
        elif rule == "always_gold":
            r = float((1 + win["gold"]).prod() - 1)
        elif rule == "rotation":
            hist = df.iloc[max(0, k - 40):k]
            trail = {a: float((1 + hist[a]).prod() - 1) for a in ("stock", "gold")}
            choice = max(trail, key=trail.get)
            r = float((1 + win[choice]).prod() - 1)
        else:
            r = 0.0
        eq *= 1 + r
        curve.append(eq)
    s = pd.Series(curve) if curve else pd.Series([1.0])
    dd = float((1 - s / s.cummax()).max() * 100) if len(s) > 1 else 0.0
    tot = (eq - 1) * 100
    return {"ret": round(tot, 1), "dd": round(dd, 1), "ratio": round(tot / max(dd, 0.1), 1)}


def main() -> int:
    df = load()
    print("sessions:", len(df), "|", df.index.min(), "->", df.index.max())
    rules = ["always_stocks", "always_gold", "static_50_50", "rotation",
             "trend_stock_to_gold", "trend_stock_to_cash"]
    out = {}
    for window, (a, b) in {"1400-1405": ("1400-01-01", "1406-01-01"),
                           "1403-1405": ("1403-01-01", "1406-01-01")}.items():
        print(f"\n=== {window} ===")
        out[window] = {}
        for rate in (CASH_RATES if window else (0.0,)):
            print(f"  cash {rate*100:.0f}%/yr")
            for rule in rules:
                r = simulate(df, rule, rate, a, b)
                key = f"{rule}@cash{int(rate*100)}"
                out[window][key] = r
                print(f"    {rule:22s} {r['ret']:+9.1f}% · افت {r['dd']:5.1f}% · نسبت {r['ratio']:7.1f}")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
