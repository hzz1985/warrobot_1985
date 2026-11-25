"""Does the selection rule matter?  Same setup, different ways of choosing 3 of N.

Entry/exit are fixed (ceiling entry with the queue, TP25/SL3/15 sessions, max 3
positions). Only the choice among the locked candidates changes:
    file      : the backtest order (baseline)
    turnover  : highest traded value first
    volratio  : highest volume vs its own 20-day median first
    rsi_low   : lowest RSI(14) first (buy the calm ones)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
D3Y = BASE / "Tse2" / "orderflow" / "data" / "daily3y"
OUT = BASE / "WarRobot" / "state" / "ranking_test.json"
TP, SL, HOLD, MAXPOS = 25.0, 3.0, 15, 3


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).rolling(n, min_periods=5).mean()
    dn = (-d.clip(upper=0)).rolling(n, min_periods=5).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


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
    dates = sorted({x for d in frames.values() for x in d["date"].tolist()})
    print("symbols:", len(frames), "| sessions:", len(dates))

    # signals per date with features
    sig = {}
    for sym, d in frames.items():
        close = d["Close"].astype(float)
        prev = close.shift(1)
        ceil = (prev * 1.03).round(0)
        locked = (d["High"] == d["Close"]) & (d["High"] >= prev * 1.029)
        turn = d["Volume"].astype(float) * close
        vr = turn / turn.shift(1).rolling(20, min_periods=10).median()
        r14 = rsi(close)
        for i in range(21, len(d) - 1):
            if not locked.iloc[i]:
                continue
            j0 = i + 1
            if float(d["High"].iloc[j0]) < float(ceil.iloc[i]):
                continue
            sig.setdefault(d["date"].iloc[i], []).append({
                "sym": sym, "entry": float(ceil.iloc[i]),
                "turnover": float(turn.iloc[i]),
                "volratio": float(vr.iloc[i]) if not pd.isna(vr.iloc[i]) else 1.0,
                "rsi": float(r14.iloc[i]) if not pd.isna(r14.iloc[i]) else 50.0,
            })
    print("signal days:", len(sig))

    def run(rule: str):
        equity, positions, trades, curve = 1.0, [], [], []
        for date in dates:
            val = equity
            still = []
            for p in positions:
                d = frames[p["sym"]]
                row = d[d["date"] == date]
                if len(row) == 0:
                    still.append(p)
                    continue
                px = float(row["Close"].iloc[0])
                p["bars"] += 1
                hit_tp = float(row["High"].iloc[0]) >= p["entry"] * (1 + TP / 100)
                hit_sl = float(row["Low"].iloc[0]) <= p["entry"] * (1 - SL / 100)
                if hit_tp or hit_sl or p["bars"] >= HOLD:
                    exit_px = p["entry"] * (1 + TP / 100) if hit_tp else (
                        p["entry"] * (1 - SL / 100) if hit_sl else px)
                    ret = (exit_px / p["entry"] - 1) * 100 - 2.0
                    equity += p["cap"] * (ret / 100)
                    trades.append({"date": date, "ret": ret})
                    continue
                still.append(p)
            positions = still
            cands = sig.get(date, [])
            if rule == "turnover":
                cands = sorted(cands, key=lambda x: -x["turnover"])
            elif rule == "volratio":
                cands = sorted(cands, key=lambda x: -x["volratio"])
            elif rule == "rsi_low":
                cands = sorted(cands, key=lambda x: x["rsi"])
            for c in cands:
                if len(positions) >= MAXPOS:
                    break
                positions.append({"sym": c["sym"], "entry": c["entry"],
                                  "cap": equity / MAXPOS, "bars": 0})
            curve.append((date, equity))
        eq = pd.DataFrame(curve, columns=["date", "eq"])
        eq["year"] = eq["date"].str[:4]
        out, pos_years = {}, 0
        for y, g in eq.groupby("year"):
            if len(g) < 5:
                continue
            r = (float(g["eq"].iloc[-1]) / float(g["eq"].iloc[0]) - 1) * 100
            peak = g["eq"].cummax()
            dd = float((1 - g["eq"] / peak).max() * 100)
            n = sum(1 for t in trades if t["date"][:4] == y)
            if n == 0:
                continue
            out[y] = {"ret": round(r, 1), "dd": round(dd, 1), "n": n}
            pos_years += 1 if r > 0 else 0
        tr = pd.DataFrame(trades)
        return {"years": out, "positive_years": pos_years,
                "trades": len(tr), "win": round(float((tr.ret > 0).mean() * 100), 1) if len(tr) else 0,
                "avg": round(float(tr.ret.mean()), 2) if len(tr) else 0}

    res = {}
    for rule in ("file", "turnover", "volratio", "rsi_low"):
        r = run(rule)
        res[rule] = r
        yrs = " · ".join(f"{y}:{v['ret']:+.0f}%" for y, v in r["years"].items())
        print(f"\n{rule:9s} سال‌های مثبت {r['positive_years']} · معاملات {r['trades']} · برد {r['win']}% · میانگین {r['avg']:+.2f}%")
        print("   ", yrs)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
