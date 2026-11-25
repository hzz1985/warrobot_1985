"""Weekly review of the CURRENT setup (queue + TP25/SL3/15).

Replaces the old band-era learner. For every bucket it reports how the setup
actually performed: number of trades, expectancy per trade, win rate.

Buckets (all measurable before entry):
    vr_*      volume vs the symbol's own 20-day median
    turn_*    traded value that day
    rsi_*     RSI(14) on the signal day
    br_*      market breadth that day (share of symbols up)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
D3Y = BASE / "Tse2" / "orderflow" / "data" / "daily3y"
OUT = BASE / "WarRobot" / "state" / "setup_review.json"
TP, SL, HOLD = 25.0, 3.0, 15


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
        frames[f.stem] = d.sort_values("date").reset_index(drop=True)

    rets = pd.concat([d.set_index("date")["Close"].astype(float).pct_change().rename(s)
                      for s, d in frames.items()], axis=1)
    breadth = (rets > 0).mean(axis=1) * 100

    rows = []
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
            entry = float(ceil.iloc[i])
            if entry <= 0:
                continue
            ret = None
            for k in range(HOLD):
                j = j0 + k
                if j >= len(d):
                    break
                if float(d["High"].iloc[j]) >= entry * (1 + TP / 100):
                    ret = TP - 2.0
                    break
                if float(d["Low"].iloc[j]) <= entry * (1 - SL / 100):
                    ret = -SL - 2.0
                    break
                ret = (float(d["Close"].iloc[j]) / entry - 1) * 100 - 2.0
            if ret is None:
                continue
            date = d["date"].iloc[i]
            rows.append({"date": date, "symbol": sym, "ret": ret,
                         "vr": float(vr.iloc[i]) if not pd.isna(vr.iloc[i]) else 1.0,
                         "turn": float(turn.iloc[i]),
                         "rsi": float(r14.iloc[i]) if not pd.isna(r14.iloc[i]) else 50.0,
                         "breadth": float(breadth.get(date, 50.0))})
    m = pd.DataFrame(rows)
    if m.empty:
        print("no trades")
        return 1

    m["vr_b"] = pd.cut(m.vr, [-1, 0.8, 1.5, 3, 1e9], labels=["vr<0.8", "vr 0.8-1.5", "vr 1.5-3", "vr>3"])
    m["turn_b"] = pd.qcut(m.turn, 3, labels=["turn_low", "turn_mid", "turn_high"])
    m["rsi_b"] = pd.cut(m.rsi, [-1, 40, 55, 70, 101], labels=["rsi<40", "rsi 40-55", "rsi 55-70", "rsi>70"])
    m["br_b"] = pd.cut(m.breadth, [-1, 35, 55, 101], labels=["br<35", "br 35-55", "br>55"])

    lines = ["گزارش ستاپ فعلی (صف + TP ۲۵٪ / SL ۳٪ / ۱۵ جلسه)", ""]
    lines.append(f"کل معاملات: {len(m):,} · برد {(m.ret>0).mean()*100:.1f}% · "
                 f"میانگین {m.ret.mean():+.2f}% · مجموع {m.ret.sum():+.0f}%")
    lines.append("")
    out = {"trades": int(len(m)), "win": round(float((m.ret > 0).mean() * 100), 1),
           "avg": round(float(m.ret.mean()), 2), "buckets": {}}
    for col, title in (("vr_b", "نسبت حجم به میانگین"), ("turn_b", "ارزش معاملات"),
                       ("rsi_b", "RSI روز سیگنال"), ("br_b", "قدرت بازار (breadth)")):
        lines.append(f"— {title} —")
        g = m.groupby(col, observed=True).ret.agg(["count", "mean", lambda s: (s > 0).mean() * 100])
        g.columns = ["n", "avg", "win"]
        for k, r in g.iterrows():
            lines.append(f"  {str(k):12s} n={int(r['n']):5d} · میانگین {r['avg']:+6.2f}% · برد {r['win']:4.1f}%")
            out["buckets"][f"{title} · {k}"] = {"n": int(r["n"]), "avg": round(float(r["avg"]), 2),
                                                "win": round(float(r["win"]), 1)}
        lines.append("")
    text = "\n".join(lines)
    print(text)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        import sys
        sys.path.insert(0, str(BASE / "TeseRobot" / "src"))
        from tsebot.alerts import send
        send("[WarRobot] گزارش هفتگی ستاپ صف", text, key="setup-review", force=True)
    except Exception as exc:
        print("email error:", str(exc)[:80])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
