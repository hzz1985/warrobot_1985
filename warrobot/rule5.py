"""Do the queue rules hold for >= 5-day lock episodes across the whole market?

Positives : the session before an episode of >=5 consecutive locked sessions
Controls  : random other sessions
Profiles  : from the tape (value, big-trade share, single-price day, last hour)

Then the same rules are scored on BOTH groups, and تاپیکو is reported separately
so we can see whether it behaves like the rest of the market.
"""
from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path

import jdatetime
import numpy as np
import pandas as pd

B = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
STATE = B / "WarRobot" / "state"
TAPE = B / "Tse2" / "orderflow" / "tape_1405.db"
INSTR = B / "Tse2" / "orderflow" / "instruments.json"
OUT = STATE / "rule5.json"
MIN_LEN = 5


def gdate(s: str) -> int:
    y, m, d = (int(x) for x in s.split("-"))
    return int(jdatetime.date(y, m, d).togregorian().strftime("%Y%m%d"))


def main() -> int:
    lw = pd.read_csv(STATE / "lock_window.csv")
    sys_path = str(B / "Tse2" / "orderflow")
    import sys as _sys
    _sys.path.insert(0, sys_path)
    import tsetmc as api
    name2code = {}

    def code_of(sym):
        if sym not in name2code:
            try:
                r = api.resolve(sym)
                name2code[sym] = str(r["ins_code"]) if r else None
            except Exception:
                name2code[sym] = None
        return name2code[sym]

    con = sqlite3.connect(TAPE)

    def profile(code, date_str):
        rows = con.execute("SELECT price, qty, h_even FROM tape WHERE ins_code=? AND d_even=? AND canceled=0",
                           (code, gdate(date_str))).fetchall()
        if len(rows) < 10:
            return None
        p = np.array([r[0] for r in rows], dtype=float)
        q = np.array([r[1] for r in rows], dtype=float)
        h = np.array([r[2] for r in rows], dtype=int)
        val = p * q
        med = float(np.median(q))
        big = q >= 5 * med
        return {"value_b": round(float(val.sum()) / 1e9, 1),
                "big_share": round(float(val[big].sum() / val.sum() * 100), 1) if val.sum() else 0.0,
                "single": int(p.min() == p.max()),
                "last_hour": round(float(val[h >= 120000].sum() / val.sum() * 100), 1) if val.sum() else 0.0}

    pos, ctrl = [], []
    for sym, g in lw.groupby("symbol"):
        code = code_of(sym)
        if not code:
            continue
        g = g.sort_values("date").reset_index(drop=True)
        runs, cur = [], []
        for i, v in enumerate(g.locked):
            if v:
                cur.append(i)
            else:
                if cur:
                    runs.append(cur)
                    cur = []
        if cur:
            runs.append(cur)
        starts = {r[0] for r in runs if len(r) >= MIN_LEN}
        for i in starts:
            if i == 0:
                continue
            pr = profile(code, g.date.iloc[i - 1])
            if pr:
                pr.update({"symbol": sym, "date": g.date.iloc[i - 1], "len": len(g.loc[i:i])})
                pos.append(pr)
        # controls: at most 3 per symbol, from days that do not start a long episode
        picks = [i for i in range(1, len(g)) if i not in starts]
        random.shuffle(picks)
        for i in picks[:3]:
            pr = profile(code, g.date.iloc[i])
            if pr:
                pr.update({"symbol": sym, "date": g.date.iloc[i]})
                ctrl.append(pr)
    con.close()

    P = pd.DataFrame(pos)
    C = pd.DataFrame(ctrl)
    print(f"positives (>= {MIN_LEN} روز): {len(P)} · controls: {len(C)}")
    if len(P):
        print(P.len.value_counts().sort_index().to_string())

    rules = {
        "ارزش ≥ ۳۰۰ میلیارد": lambda x: x["value_b"] >= 300,
        "سهم بزرگ ≥ ۳۰٪": lambda x: x["big_share"] >= 30,
        "تک‌قیمت (قفل)": lambda x: x["single"] == 1,
        "ارزش ≥ ۳۰۰ و بزرگ ≥ ۳۰٪": lambda x: x["value_b"] >= 300 and x["big_share"] >= 30,
        "تک‌قیمت یا بزرگ ≥ ۳۰٪": lambda x: x["single"] == 1 or x["big_share"] >= 30,
        "ارزش ≥ ۵۰۰ و تک‌قیمت": lambda x: x["value_b"] >= 500 and x["single"] == 1,
    }
    lines = []
    for name, f in rules.items():
        h = int(sum(1 for _, x in P.iterrows() if f(x))) if len(P) else 0
        fa = int(sum(1 for _, x in C.iterrows() if f(x))) if len(C) else 0
        prec = h / (h + fa) * 100 if (h + fa) else 0
        rec = h / len(P) * 100 if len(P) else 0
        base = len(P) / (len(P) + len(C)) * 100 if (len(P) + len(C)) else 0
        lines.append({"rule": name, "hit": h, "n": len(P), "false": fa, "m": len(C),
                      "precision": round(prec, 1), "recall": round(rec, 1), "base": round(base, 1)})
        print(f"  {name:30s} برد {h}/{len(P)} · کاذب {fa}/{len(C)} · دقت {prec:.0f}% · پایه {base:.0f}%")

    tap = P[P.symbol == "تاپیکو"] if len(P) else pd.DataFrame()
    print(f"\nتاپیکو در گروه نمونه: {len(tap)} مورد")
    if len(tap):
        print(tap[["date", "len", "value_b", "big_share", "single", "last_hour"]].to_string(index=False))

    OUT.write_text(json.dumps({"min_len": MIN_LEN, "positives": len(P), "controls": len(C),
                               "rules": lines,
                               "tapico": tap.to_dict("records") if len(tap) else []},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
