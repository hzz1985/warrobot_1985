"""Window behaviour (5 sessions) before a >=5-day lock episode, market-wide.

Features are built from the daily bars only, so the test is fast and covers every
symbol from the start of 1405:
    * single-price days in the window (High == Close)
    * turnover of the window relative to the symbol's own 60-day median
    * 5-day return and how often the stock closed in the top of its range
    * number of locked days inside the window

Positive : the session that starts a >=5 consecutive locked-session episode
Control  : every other session
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
DAILY = BASE / "TeseRobot" / "data" / "daily"
OUT = BASE / "WarRobot" / "state" / "window5.json"
MIN_EPISODE = 5
WIN = 5


def main() -> int:
    pos_rows, ctl_rows = [], []
    for f in DAILY.glob("*_daily.csv"):
        sym = f.name[:-len("_daily.csv")]
        try:
            d = pd.read_csv(f, dtype={"date": str}).sort_values("date").reset_index(drop=True)
        except Exception:
            continue
        if len(d) < 80:
            continue
        close = d["Close"].astype(float)
        hi, lo = d["High"].astype(float), d["Low"].astype(float)
        prev = close.shift(1)
        d["locked"] = ((hi == close) & (hi >= prev * 1.029)).astype(int)
        turn = d["Volume"].astype(float) * close
        base = turn.shift(1).rolling(60, min_periods=30).median()

        rng = (hi - lo)
        cpos = ((close - lo) / rng.replace(0, np.nan)).where(rng > 0, np.where(d["locked"] == 1, 1.0, 0.5))

        runs, cur = [], []
        for i, v in enumerate(d["locked"]):
            if v:
                cur.append(i)
            else:
                if cur:
                    runs.append(cur)
                    cur = []
        if cur:
            runs.append(cur)
        starts = {r[0] for r in runs if len(r) >= MIN_EPISODE}
        if not starts:
            continue

        for i in range(WIN, len(d)):
            if d["date"].iloc[i] < "1405-01-01":
                continue
            w = slice(i - WIN, i)
            feat = {
                "single_days": int((hi[w] == close[w]).sum()),
                "lock_days": int(d["locked"].iloc[w].sum()),
                "ret5": round(float(close.iloc[i - 1] / close.iloc[i - 1 - WIN] - 1) * 100, 2),
                "turn_ratio": round(float(turn.iloc[w].mean() / base.iloc[i - 1]), 2) if base.iloc[i - 1] else None,
                "cpos5": round(float(cpos.iloc[w].mean()), 2) if not pd.isna(cpos.iloc[w].mean()) else None,
            }
            feat["symbol"] = sym
            (pos_rows if i in starts else ctl_rows).append(feat)

    P, C = pd.DataFrame(pos_rows), pd.DataFrame(ctl_rows)
    print(f"positives={len(P)} controls={len(C)} base={len(P)/(len(P)+len(C))*100:.1f}%")

    def rule_report(name, f):
        hp = int(f(P).sum()) if len(P) else 0
        hc = int(f(C).sum()) if len(C) else 0
        prec = hp / (hp + hc) * 100 if (hp + hc) else 0
        rec = hp / len(P) * 100 if len(P) else 0
        print(f"  {name:34s} برد {hp:4d}/{len(P)} · کاذب {hc:4d}/{len(C)} · دقت {prec:4.0f}% · پوشش {rec:3.0f}%")
        return {"rule": name, "hit": hp, "false": hc, "precision": round(prec, 1), "recall": round(rec, 1)}

    out = []
    print("قواعد ۵ روزه:")
    out.append(rule_report("تک‌قیمت ≥ ۲ روز در ۵ روز", lambda x: x.single_days >= 2))
    out.append(rule_report("تک‌قیمت ≥ ۳ روز در ۵ روز", lambda x: x.single_days >= 3))
    out.append(rule_report("قفل ≥ ۲ روز در ۵ روز", lambda x: x.lock_days >= 2))
    out.append(rule_report("نسبت ارزش ≤ ۰٫۸ (عرضه کم)", lambda x: x.turn_ratio <= 0.8))
    out.append(rule_report("تک‌قیمت≥۲ و نسبت ارزش≤۱", lambda x: (x.single_days >= 2) & (x.turn_ratio <= 1.0)))
    out.append(rule_report("قفل≥۲ و بازده ۵روزه > ۰", lambda x: (x.lock_days >= 2) & (x.ret5 > 0)))

    OUT.write_text(json.dumps({"min_episode": MIN_EPISODE, "window": WIN,
                               "positives": len(P), "controls": len(C),
                               "base_pct": round(len(P) / (len(P) + len(C)) * 100, 1),
                               "rules": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
