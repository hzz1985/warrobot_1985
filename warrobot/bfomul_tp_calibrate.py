"""BFomul calibration v2 — target = reaching TP within 7 sessions.

For EVERY symbol (not just those with a long queue):
  * entry days = sessions that closed locked on the ceiling (same rule as before)
  * label      = did the trade hit TP before SL within `HOLD` sessions?
  * structure  = the same BFomul matrix (RSI range · Bollinger · ceiling turnover ·
                 ceiling share) with per-symbol thresholds
  * acceptance = hit rate >= 70% with at least 5 signals
  * scoring    = hit_rate, then signals, then return per holding day

Outputs: Desktop/bfomul_tp.xlsx  (sheets: formulas, rejected)
         Desktop/bfomul_tp_wlist.xlsx (symbols with no usable data)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import jdatetime
import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
DAILY = BASE / "TeseRobot" / "data" / "daily"
D3Y = BASE / "Tse2" / "orderflow" / "data" / "daily3y"
TAPE = BASE / "Tse2" / "orderflow" / "tape_1405.db"
OUT = Path(r"C:\Users\Administrator\Desktop\bfomul_tp.xlsx")
WL = Path(r"C:\Users\Administrator\Desktop\bfomul_tp_wlist.xlsx")
STATE = BASE / "WarRobot" / "state"

HOLD = 7
TPS = [9.0, 12.0, 15.0]
SL = 4.0
MIN_SIG = 5
MIN_HIT = 70.0

RSI_MINS = [30, 40]
RSI_MAXS = [70, 80]
BB_MAXS = [0.80, 0.90]
CEIL_MAXS = [100, 300, 600, 1e9]
SHARE_MINS = [0, 90]


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).rolling(n, min_periods=5).mean()
    dn = (-d.clip(upper=0)).rolling(n, min_periods=5).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def load(sym: str):
    for p in (DAILY / f"{sym}_daily.csv", D3Y / f"{sym}.csv"):
        if p.exists():
            try:
                return pd.read_csv(p, dtype={"date": str}).sort_values("date").reset_index(drop=True)
            except Exception:
                return None
    return None


def gdate(s: str) -> int:
    y, m, d = (int(x) for x in s.split("-"))
    return int(jdatetime.date(y, m, d).togregorian().strftime("%Y%m%d"))


def outcome(d: pd.DataFrame, i: int, tp: float):
    """(hit, days, ret) for a trade entered at the open of i+1."""
    j0 = i + 1
    if j0 >= len(d):
        return None
    entry = float(d["Open"].iloc[j0])
    if entry <= 0:
        return None
    for k in range(HOLD):
        j = j0 + k
        if j >= len(d):
            break
        if float(d["High"].iloc[j]) >= entry * (1 + tp / 100):
            return 1, k + 1, tp - 2.0
        if float(d["Low"].iloc[j]) <= entry * (1 - SL / 100):
            return 0, k + 1, -SL - 2.0
    last = float(d["Close"].iloc[min(j0 + HOLD - 1, len(d) - 1)])
    return 0, HOLD, (last / entry - 1) * 100 - 2.0


def main() -> int:
    con = sqlite3.connect(TAPE)
    has_dc = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='day_ceiling'").fetchone()
    sys.path.insert(0, str(BASE / "Tse2" / "orderflow"))
    import tsetmc as api

    symbols = sorted({f.name[:-len("_daily.csv")] for f in DAILY.glob("*_daily.csv")} |
                     {f.stem for f in D3Y.glob("*.csv")})
    print(f"symbols: {len(symbols)}", flush=True)

    good, bad, wl = [], [], []
    for n_, sym in enumerate(symbols, 1):
        d = load(sym)
        if d is None or len(d) < 80:
            wl.append({"symbol": sym, "reason": "داده ناکافی"})
            continue
        d = d[d["date"] >= "1405-01-01"].reset_index(drop=True)
        if len(d) < 30:
            wl.append({"symbol": sym, "reason": "بازه کوتاه"})
            continue
        close = d["Close"].astype(float)
        r14 = rsi(close)
        ma20 = close.rolling(20, min_periods=10).mean()
        sd = close.rolling(20, min_periods=10).std()
        bb = (close - (ma20 - 2 * sd)) / ((4 * sd).replace(0, np.nan))
        prev = close.shift(1)
        locked = (d["High"] == d["Close"]) & (d["High"] >= prev * 1.029)

        code = None
        try:
            m = api.resolve(sym)
            code = m["ins_code"] if m else None
        except Exception:
            code = None

        rows = []
        for i in range(1, len(d) - 1):
            if not locked.iloc[i] or pd.isna(r14.iloc[i]) or pd.isna(bb.iloc[i]):
                continue
            cel = share = None
            if code and has_dc:
                try:
                    q = con.execute("SELECT cel, tot FROM day_ceiling WHERE ins_code=? AND d_even=?",
                                    (code, gdate(d["date"].iloc[i]))).fetchone()
                    if q and q[1]:
                        cel, share = (q[0] or 0), (q[0] or 0) / q[1] * 100
                except Exception:
                    pass
            if cel is None:
                cel = float(d["Volume"].iloc[i] * close.iloc[i])
                share = 100.0
            outs = {tp: outcome(d, i, tp) for tp in TPS}
            row = {"rsi": r14.iloc[i], "bb": bb.iloc[i], "ceil": cel / 1e9, "share": share}
            for tp in TPS:
                o = outs[tp]
                row[f"hit{tp}"] = o[0] if o else np.nan
                row[f"days{tp}"] = o[1] if o else np.nan
                row[f"ret{tp}"] = o[2] if o else np.nan
            rows.append(row)
        if not rows:
            wl.append({"symbol": sym, "reason": "بدون روز قفل"})
            continue
        t = pd.DataFrame(rows)

        best = None
        for tp in TPS:
            sub = t.dropna(subset=[f"hit{tp}"])
            if len(sub) < MIN_SIG:
                continue
            for rl in RSI_MINS:
                for rh in RSI_MAXS:
                    if rh <= rl:
                        continue
                    for bm in BB_MAXS:
                        for cm in CEIL_MAXS:
                            for sm in SHARE_MINS:
                                sig = (sub.rsi >= rl) & (sub.rsi <= rh) & (sub.bb < bm) & \
                                      (sub.ceil < cm) & (sub.share >= sm)
                                n = int(sig.sum())
                                if n < MIN_SIG:
                                    continue
                                hit = float(sub.loc[sig, f"hit{tp}"].mean() * 100)
                                if hit < MIN_HIT:
                                    continue
                                days = float(sub.loc[sig, f"days{tp}"].mean())
                                ret = float(sub.loc[sig, f"ret{tp}"].mean())
                                per_day = ret / max(days, 1)
                                cand = (round(hit, 1), n, round(per_day, 3), tp, rl, rh, bm, cm, sm,
                                        round(ret, 2), round(days, 1))
                                if best is None or cand > best:
                                    best = cand
        if best:
            hit, n, per_day, tp, rl, rh, bm, cm, sm, ret, days = best
            good.append({"symbol": sym, "tp": tp, "sl": SL, "hold": HOLD,
                         "rsi_min": rl, "rsi_max": rh, "bb_max": bm,
                         "ceil_max_b": None if cm >= 1e9 else cm, "share_min": sm,
                         "signals": n, "hit_rate": hit, "avg_return": ret,
                         "avg_days": days, "return_per_day": per_day})
        else:
            bad.append({"symbol": sym, "locked_days": len(t),
                        "reason": "هیچ ترکیبی به دقت ۷۰٪ نرسید"})
        if n_ % 50 == 0:
            print(f"…{n_}/{len(symbols)} good={len(good)} bad={len(bad)} wl={len(wl)}", flush=True)
            try:
                with pd.ExcelWriter(OUT, engine="openpyxl") as xw:
                    pd.DataFrame(good).to_excel(xw, sheet_name="formulas", index=False)
                    pd.DataFrame(bad).to_excel(xw, sheet_name="rejected", index=False)
            except Exception:
                pass

    con.close()
    with pd.ExcelWriter(OUT, engine="openpyxl") as xw:
        pd.DataFrame(good).to_excel(xw, sheet_name="formulas", index=False)
        pd.DataFrame(bad).to_excel(xw, sheet_name="rejected", index=False)
    pd.DataFrame(wl).to_excel(WL, index=False)
    print(f"done: good={len(good)} rejected={len(bad)} wlist={len(wl)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
