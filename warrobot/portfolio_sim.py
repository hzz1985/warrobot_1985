"""Portfolio simulation of the queue setup (max 3 concurrent positions).

Sequential capital, equal weight, entry at the ceiling when the queue fills,
exit TP25/SL3 or after 15 sessions. Produces annual returns and the max drawdown
— the numbers the acceptance gate needs.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
D3Y = BASE / "Tse2" / "orderflow" / "data" / "daily3y"
OUT = BASE / "WarRobot" / "state" / "portfolio_sim.json"
TP, SL, HOLD, MAXPOS = 25.0, 3.0, 15, 3


def main() -> int:
    frames = {}
    for f in D3Y.glob("*.csv"):
        try:
            d = pd.read_csv(f, dtype={"date": str})
        except Exception:
            continue
        if len(d) < 200:
            continue
        frames[f.stem] = d.sort_values("date").reset_index(drop=True)
    dates = sorted({x for d in frames.values() for x in d["date"].tolist()})
    print("symbols:", len(frames), "| sessions:", len(dates))
    di = {d: i for i, d in enumerate(dates)}

    # pre-compute signals: (date, symbol, ceiling) where the queue filled next session
    signals = {}
    for sym, d in frames.items():
        close = d["Close"].astype(float)
        prev = close.shift(1)
        ceil = (prev * 1.03).round(0)
        locked = (d["High"] == d["Close"]) & (d["High"] >= prev * 1.029)
        for i in range(2, len(d) - 1):
            if not locked.iloc[i]:
                continue
            j0 = i + 1
            if float(d["High"].iloc[j0]) < float(ceil.iloc[i]):
                continue
            signals.setdefault(d["date"].iloc[i], []).append((sym, float(ceil.iloc[i]), j0))

    equity, positions, trades = 1.0, [], []
    curve = []
    per_year = {}
    for k, date in enumerate(dates):
        # mark to market
        val = equity
        still = []
        for p in positions:
            d = frames[p["sym"]]
            row = d[d["date"] == date]
            if len(row) == 0:
                still.append(p)
                continue
            px = float(row["Close"].iloc[0])
            pnl = (px / p["entry"] - 1)
            val += p["cap"] * pnl
            p["last"] = px
            p["bars"] += 1
            hit_tp = float(row["High"].iloc[0]) >= p["entry"] * (1 + TP / 100)
            hit_sl = float(row["Low"].iloc[0]) <= p["entry"] * (1 - SL / 100)
            if hit_tp or hit_sl or p["bars"] >= HOLD:
                exit_px = p["entry"] * (1 + TP / 100) if hit_tp else (
                    p["entry"] * (1 - SL / 100) if hit_sl else px)
                ret = (exit_px / p["entry"] - 1) * 100 - 2.0
                equity += p["cap"] * (ret / 100)
                trades.append({"date": date, "sym": p["sym"], "ret": ret})
                continue
            still.append(p)
        positions = still

        # open new positions
        for sym, entry, j0 in signals.get(date, []):
            if len(positions) >= MAXPOS:
                break
            cap = equity / MAXPOS
            positions.append({"sym": sym, "entry": entry, "cap": cap, "bars": 0, "last": entry})
        curve.append((date, equity))

    eq = pd.DataFrame(curve, columns=["date", "eq"])
    eq["year"] = eq["date"].str[:4]
    out = {}
    print("\nشبیه‌سازی پرتفوی (حداکثر ۳ پوزیشن):")
    for y, g in eq.groupby("year"):
        first, last = float(g["eq"].iloc[0]), float(g["eq"].iloc[-1])
        r = (last / first - 1) * 100
        peak = g["eq"].cummax()
        dd = float((1 - g["eq"] / peak).max() * 100)
        n = sum(1 for t in trades if t["date"][:4] == y)
        out[y] = {"return": round(r, 1), "dd": round(dd, 1), "trades": n}
        print(f"  {y}: بازده {r:+7.1f}% · افت {dd:5.1f}% · معاملات {n:4d}")
    if trades:
        tr = pd.DataFrame(trades)
        print(f"\nکل معاملات: {len(tr)} · برد {(tr.ret>0).mean()*100:.1f}% · میانگین {tr.ret.mean():+.2f}%")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
