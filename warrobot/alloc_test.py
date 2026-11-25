"""First shot at a cross-asset allocation test.

Assets (all daily, no look-ahead):
    stocks : equal-weight return of the local TSE daily files
    gold   : tgju 18k gold gram (the return a gold fund tracks)
    cash   : flat 0% (deliberately conservative: no invented yield)

Rule under test (monthly decision, signal from the close BEFORE the period):
    hold the asset whose trailing N-day return is highest; ties -> stocks.
Baseline: always stocks (the current bot behaviour).
Compared per Jalali year: total return, max drawdown, and the share of periods
each asset was chosen.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
D3Y = BASE.parent / "Tse2" / "orderflow" / "data" / "daily3y"
MACRO = BASE / "data" / "macro"
OUT = BASE / "state" / "alloc_test.json"

LOOKBACK = 20      # signal window (sessions)
HOLD = 20          # rebalance period (sessions)


def load_assets():
    rets = []
    for f in D3Y.glob("*.csv"):
        try:
            d = pd.read_csv(f, dtype={"date": str})
        except Exception:
            continue
        if len(d) < 250:
            continue
        g = d.set_index("date")["Close"].astype(float)
        rets.append(g.pct_change().rename(f.stem))
    stock = pd.concat(rets, axis=1).mean(axis=1)
    idx = (1 + stock.fillna(0)).cumprod()

    def macro(name: str) -> pd.Series:
        d = pd.read_csv(MACRO / f"{name}.csv", dtype={"date": str})
        j = d["jalali"].str.replace("/", "-", regex=False)
        # jalali like 1405-08-10 -> convert to the same key space as the stock files
        s = pd.Series(d["close"].astype(float).values, index=j.values)
        return s[~s.index.duplicated(keep="last")].sort_index()

    usd, gold = macro("usd"), macro("gold18")
    # map jalali dates onto the stock trading days
    days = pd.Index(idx.index).astype(str)
    out = pd.DataFrame(index=days)
    out["stock"] = stock.reindex(days).fillna(0)
    out["gold"] = gold.pct_change().reindex(days).ffill().fillna(0)
    out["usd"] = usd.pct_change().reindex(days).ffill().fillna(0)
    return out, idx


def backtest(df: pd.DataFrame, lookback: int, hold: int):
    days = list(df.index)
    equity = 1.0
    curve, picks = [], []
    for k in range(200, len(days) - hold, hold):
        hist = df.iloc[:k]
        trail = {a: float((1 + hist[a].tail(lookback)).prod() - 1) for a in ("stock", "gold")}
        choice = max(trail, key=trail.get)
        window = df.iloc[k:k + hold]
        r = float((1 + window[choice]).prod() - 1)
        equity *= 1 + r
        curve.append((days[k + hold - 1], equity))
        picks.append({"date": days[k + hold - 1], "asset": choice, "ret": round(r * 100, 2)})
    return pd.DataFrame(curve, columns=["date", "eq"]), pd.DataFrame(picks)


def yearly(eq: pd.DataFrame):
    if eq.empty:
        return {}
    eq = eq.copy()
    eq["year"] = eq["date"].astype(str).str[:4]
    out = {}
    for y, g in eq.groupby("year"):
        first, last = float(g["eq"].iloc[0]), float(g["eq"].iloc[-1])
        peak = g["eq"].cummax()
        out[y] = {"ret": round((last / first - 1) * 100, 1),
                  "dd": round(float((1 - g["eq"] / peak).max() * 100), 1)}
    return out


def main() -> int:
    df, idx = load_assets()
    print("sessions:", len(df), "| span:", df.index.min(), "->", df.index.max())

    # baselines
    bh = (1 + df["stock"]).cumprod()
    gold_bh = (1 + df["gold"]).cumprod()
    print(f"buy&hold stocks: {(bh.iloc[-1]-1)*100:+.0f}%  · gold: {(gold_bh.iloc[-1]-1)*100:+.0f}%")

    eq, picks = backtest(df, LOOKBACK, HOLD)
    peak = eq["eq"].cummax()
    print(f"rotation: {(eq['eq'].iloc[-1]-1)*100:+.0f}% · max DD {float((1-eq['eq']/peak).max()*100):.0f}%")
    print("picks:", picks["asset"].value_counts().to_dict())

    def yearly_ret(series: pd.Series) -> dict:
        out, yr = {}, pd.Series(series.index).astype(str).str[:4].values
        tmp = pd.DataFrame({"y": yr, "r": series.values}, index=series.index)
        for y, g in tmp.groupby("y"):
            out[y] = {"ret": round(float((1 + g["r"]).prod() - 1) * 100, 1)}
        return out

    res = {"buy_hold_stocks": round(float((bh.iloc[-1] - 1) * 100), 1),
           "buy_hold_gold": round(float((gold_bh.iloc[-1] - 1) * 100), 1),
           "rotation": {"total": round(float((eq["eq"].iloc[-1] - 1) * 100), 1),
                        "years": yearly(eq),
                        "picks": picks["asset"].value_counts().to_dict()},
           "buy_hold_years": yearly_ret(df["stock"]),
           "buy_hold_gold_years": yearly_ret(df["gold"])}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nper year (rotation | buy&hold stocks):")
    for y in sorted(res["rotation"]["years"]):
        a = res["rotation"]["years"][y]
        b = res["buy_hold_years"].get(y, {})
        g2 = res["buy_hold_gold_years"].get(y, {})
        print(f"  {y}: چرخش {a['ret']:+7.1f}% (dd {a['dd']:4.1f}%) | سهم {b.get('ret', 0):+7.1f}% | طلا {g2.get('ret', 0):+7.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
