"""Fix the ceiling test (tick rounding) and derive a rule for تاپیکو's long queues.

Locked test (robust, no tick table needed):
    a session is LOCKED when it closed at its own daily high AND that high is at
    least ~2.9% above the previous close (the price limit), i.e. the stock traded
    at a single ceiling price.

Then, for every episode of >= 3 consecutive locked sessions, the session BEFORE it
is profiled from the tape (value, big-trade share, last-hour share, single price)
and compared with all other sessions -> a candidate rule is scored on hit rate.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import jdatetime
import numpy as np
import pandas as pd

B = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
DAILY = B / "TeseRobot" / "data" / "daily" / "تاپیکو_daily.csv"
TAPE = B / "Tse2" / "orderflow" / "tape_1405.db"
OUT = B / "WarRobot" / "state" / "tapico_rule.json"
IC = "22560050433388046"


def gdate(s: str) -> int:
    y, m, d = (int(x) for x in s.split("-"))
    return int(jdatetime.date(y, m, d).togregorian().strftime("%Y%m%d"))


def main() -> int:
    d = pd.read_csv(DAILY, dtype={"date": str}).sort_values("date").reset_index(drop=True)
    prev = d["Close"].shift(1)
    d["locked"] = ((d["High"] == d["Close"]) & (d["High"] >= prev * 1.029)).astype(int)
    d = d[d["date"] >= "1405-01-01"].reset_index(drop=True)

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
    long_runs = [r for r in runs if len(r) >= 3]
    print(f"sessions={len(d)} locked={int(d.locked.sum())} episodes={len(runs)} long(>=3)={len(long_runs)}")
    for r in long_runs:
        print(f"  {len(r)} روز: {d.date.iloc[r[0]]} → {d.date.iloc[r[-1]]}")

    con = sqlite3.connect(TAPE)

    def profile(date_str: str):
        rows = con.execute("SELECT price, qty, h_even FROM tape WHERE ins_code=? AND d_even=? AND canceled=0",
                           (IC, gdate(date_str))).fetchall()
        if not rows:
            return None
        p = np.array([r[0] for r in rows], dtype=float)
        q = np.array([r[1] for r in rows], dtype=float)
        h = np.array([r[2] for r in rows], dtype=int)
        val = p * q
        med = float(np.median(q))
        big = q >= 5 * med
        return {
            "value_b": round(float(val.sum()) / 1e9, 1),
            "big_share": round(float(val[big].sum() / val.sum() * 100), 1) if val.sum() else 0,
            "last_hour": round(float(val[h >= 120000].sum() / val.sum() * 100), 1) if val.sum() else 0,
            "single_price": int(p.min() == p.max()),
            "trades": len(rows),
        }

    pos = []
    for r in long_runs:
        i = r[0]
        if i == 0:
            continue
        cd = d.date.iloc[i - 1]
        pr = profile(cd)
        if pr:
            pr["date"] = cd
            pr["episode"] = len(r)
            pos.append(pr)
    print("\nروز قبل صف‌های بلند:")
    for x in pos:
        print(f"  {x['date']} · صف {x['episode']} روزه · ارزش {x['value_b']}B · بزرگ {x['big_share']}% · "
              f"ساعت آخر {x['last_hour']}% · تک‌قیمت {x['single_price']}")

    # control: every other session before a NON-long day
    long_start = {r[0] for r in long_runs}
    ctrl = []
    for i in range(1, len(d)):
        if i in long_start:
            continue
        pr = profile(d.date.iloc[i])
        if pr:
            pr["date"] = d.date.iloc[i]
            ctrl.append(pr)
    print(f"\nکنترل: {len(ctrl)} روز")

    def score(rule):
        hit = sum(1 for x in pos if rule(x))
        false = sum(1 for x in ctrl if rule(x))
        return hit, len(pos), false, len(ctrl)

    rules = {
        "ارزش ≥ ۳۰۰ میلیارد": lambda x: x["value_b"] >= 300,
        "سهم بزرگ ≥ ۳۰٪": lambda x: x["big_share"] >= 30,
        "تک‌قیمت (بسته روی سقف)": lambda x: x["single_price"] == 1,
        "ارزش≥۳۰۰ و بزرگ≥۳۰٪": lambda x: x["value_b"] >= 300 and x["big_share"] >= 30,
        "تک‌قیمت یا بزرگ≥۳۰٪": lambda x: x["single_price"] == 1 or x["big_share"] >= 30,
        "ارزش≥۳۰۰ و (تک‌قیمت یا بزرگ≥۳۰٪)": lambda x: x["value_b"] >= 300 and (x["single_price"] == 1 or x["big_share"] >= 30),
    }
    print("\nارزیابی قواعد:")
    best = None
    for name, rule in rules.items():
        h, n, f, m = score(rule)
        precision = h / (h + f) * 100 if (h + f) else 0
        recall = h / n * 100 if n else 0
        print(f"  {name:38s} برد {h}/{n} · هشدار کاذب {f}/{m} · دقت {precision:.0f}% · پوشش {recall:.0f}%")
        if best is None or (h, -f) > (best[1], -best[2]):
            best = (name, h, f)

    payload = {"locked_test": "High==Close and High >= prev*1.029",
               "episodes": len(runs), "long_episodes": len(long_runs),
               "long_runs": [{"start": d.date.iloc[r[0]], "end": d.date.iloc[r[-1]], "len": len(r)}
                             for r in long_runs],
               "before_long": pos, "controls": len(ctrl), "best_rule": best[0]}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
