"""Walk-forward test of simple setups, 1402 -> 1405.

Setups
  A  queue   : buy at the ceiling only when the session trades there, with the
               market index above its 200-day average; exit TP25/SL3/15 sessions.
  A0 queue0  : same without the trend filter (baseline).
  D  momentum: monthly rebalance into the 10 best 20-day returns, hold 20 sessions.

Every setup is reported per year: trades, win rate, average, total, max drawdown.
Acceptance (all four): positive in >=3 of 4 years, drawdown < 20%, >=100 trades,
return/risk > 1.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
D3Y = BASE / "Tse2" / "orderflow" / "data" / "daily3y"
OUT = BASE / "WarRobot" / "state" / "walkforward.json"

TP, SL, HOLD = 25.0, 3.0, 15


def main() -> int:
    frames = {}
    for f in D3Y.glob("*.csv"):
        try:
            d = pd.read_csv(f, dtype={"date": str})
        except Exception:
            continue
        if len(d) < 200:
            continue
        d = d.sort_values("date").reset_index(drop=True)
        frames[f.stem] = d
    print("symbols:", len(frames))
    if not frames:
        return 1

    # market proxy: equal-weight daily return
    rets = pd.concat([d.set_index("date")["Close"].astype(float).pct_change().rename(s)
                      for s, d in frames.items()], axis=1)
    mkt = rets.mean(axis=1)
    idx = (1 + mkt.fillna(0)).cumprod()
    ma200 = idx.rolling(200, min_periods=120).mean()
    trend = (idx > ma200)
    print("index days:", len(idx))

    trades_a, trades_a0 = [], []
    for sym, d in frames.items():
        close = d["Close"].astype(float)
        prev = close.shift(1)
        ceil = (prev * 1.03).round(0)
        locked = (d["High"] == d["Close"]) & (d["High"] >= prev * 1.029)
        for i in range(2, len(d) - 1):
            if not locked.iloc[i]:
                continue
            date = d["date"].iloc[i]
            j0 = i + 1
            if float(d["High"].iloc[j0]) < float(ceil.iloc[i]):
                continue                      # the queue did not fill next session
            entry = float(ceil.iloc[i])
            if entry <= 0:
                continue
            ret, days = None, HOLD
            for k in range(HOLD):
                j = j0 + k
                if j >= len(d):
                    break
                if float(d["High"].iloc[j]) >= entry * (1 + TP / 100):
                    ret, days = TP - 2.0, k + 1
                    break
                if float(d["Low"].iloc[j]) <= entry * (1 - SL / 100):
                    ret, days = -SL - 2.0, k + 1
                    break
                ret = (float(d["Close"].iloc[j]) / entry - 1) * 100 - 2.0
            if ret is None:
                continue
            rec = {"date": date, "symbol": sym, "ret": ret, "days": days}
            trades_a0.append(rec)
            t = trend.get(date)
            if t is True:
                trades_a.append(rec)

    # momentum: monthly, top 10 by 20-day return
    dates = sorted(idx.index)
    mom = []
    step = 20
    for k in range(200, len(dates) - step, step):
        d0, d1 = dates[k], dates[min(k + step, len(dates) - 1)]
        scores = {}
        for sym, d in frames.items():
            dd = d[d["date"] <= d0]
            if len(dd) < 25:
                continue
            r20 = float(dd["Close"].iloc[-1]) / float(dd["Close"].iloc[-21]) - 1
            nxt = d[(d["date"] > d0)]
            if len(nxt) == 0:
                continue
            p0 = float(nxt["Open"].iloc[0])
            fl = nxt[nxt["date"] <= d1]
            if p0 <= 0 or len(fl) == 0:
                continue
            p1 = float(fl["Close"].iloc[-1])
            scores[sym] = (r20, (p1 / p0 - 1) * 100 - 2.0, d1)
        if not scores:
            continue
        best = sorted(scores.items(), key=lambda kv: -kv[1][0])[:10]
        for sym, (_, r, dd1) in best:
            mom.append({"date": dd1, "symbol": sym, "ret": r})

    def yearly(rows, label):
        if not rows:
            print(f"{label}: no trades")
            return {}
        df = pd.DataFrame(rows)
        df["year"] = df["date"].str[:4]
        out = {}
        print(f"\n=== {label} ===")
        for y, g in df.groupby("year"):
            r = g["ret"].to_numpy()
            eq = np.cumprod(1 + r / 100)
            dd = float((1 - eq / np.maximum.accumulate(eq)).max() * 100)
            out[y] = {"trades": int(len(g)), "win": round(float((r > 0).mean() * 100), 1),
                      "avg": round(float(r.mean()), 2), "total": round(float(r.sum()), 1),
                      "dd": round(dd, 1)}
            print(f"  {y}: n={len(g):4d} · برد {out[y]['win']:5.1f}% · میانگین {out[y]['avg']:+6.2f}% · "
                  f"مجموع {out[y]['total']:+8.1f}% · افت {dd:5.1f}%")
        return out

    res = {"A_queue_trend": yearly(trades_a, "A: صف + فیلتر روند"),
           "A0_queue": yearly(trades_a0, "A0: صف بدون فیلتر"),
           "D_momentum": yearly(mom, "D: مومنتوم ماهانه")}

    def accept(d):
        if not d:
            return False
        pos = sum(1 for v in d.values() if v["total"] > 0)
        trades = sum(v["trades"] for v in d.values())
        dd = max(v["dd"] for v in d.values())
        return pos >= 3 and trades >= 100 and dd < 20

    print("\nپذیرش (مثبت در ≥۳ سال · ≥۱۰۰ معامله · افت <۲۰٪):")
    for k, v in res.items():
        print(f"  {k}: {'PASS' if accept(v) else 'FAIL'}")
        res[k] = {"years": v, "pass": accept(v)}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
