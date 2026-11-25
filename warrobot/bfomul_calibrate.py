"""Calibrate BFomul per symbol (batches of 5), 1405 -> today.

Step 1 (once): precompute, per symbol-day, total turnover and the turnover that
        traded at the day's top price -> table `day_ceiling` in tape_1405.db.
Step 2: for every symbol, find the lock episodes (High==Close and +2.9%),
        keep the ones with >=5 consecutive days, and grid-search the four BFomul
        thresholds so that precision >= 75% while keeping the coverage as high as
        possible.
Step 3: write two workbooks on the Desktop:
        bfomul_by_symbol.xlsx  (one row per calibrated symbol)
        WList.xlsx             (symbols with no >=5-day queue in the window)
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
STATE = BASE / "WarRobot" / "state"
OUT_OK = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\data\bfomul_by_symbol.xlsx")
OUT_WL = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\data\WList.xlsx")
START = "1405-01-01"
MIN_LEN = 5
BATCH = 5

RSI_MINS = [30, 35, 40]
RSI_MAXS = [70, 75, 80]
BB_MAXS = [0.80, 0.85, 0.90]
CEIL_MAXS = [300, 400, 500, 600, 900, 1e9]
SHARE_MINS = [0, 90, 95]


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).rolling(n, min_periods=5).mean()
    dn = (-d.clip(upper=0)).rolling(n, min_periods=5).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def precompute(con: sqlite3.Connection) -> None:
    has = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='day_ceiling'").fetchone()
    if has:
        print("day_ceiling exists")
        return
    print("precomputing day_ceiling (this takes a few minutes)…", flush=True)
    con.execute("CREATE TABLE day_stats AS SELECT ins_code, d_even, SUM(price*qty) tot, MAX(price) mx "
                "FROM tape WHERE canceled=0 GROUP BY ins_code, d_even")
    con.execute("CREATE INDEX ix_ds ON day_stats(ins_code, d_even)")
    con.commit()
    con.execute("""CREATE TABLE day_ceiling AS
        SELECT t.ins_code AS ins_code, t.d_even AS d_even,
               SUM(t.price*t.qty) AS cel, s.tot AS tot, s.mx AS mx
        FROM tape t JOIN day_stats s
          ON s.ins_code=t.ins_code AND s.d_even=t.d_even
        WHERE t.canceled=0 AND t.price >= s.mx*0.999
        GROUP BY t.ins_code, t.d_even""")
    con.execute("CREATE INDEX ix_dc ON day_ceiling(ins_code, d_even)")
    con.commit()
    print("day_ceiling ready", flush=True)


def gdate(s: str) -> int:
    y, m, d = (int(x) for x in s.split("-"))
    return int(jdatetime.date(y, m, d).togregorian().strftime("%Y%m%d"))


def load(sym: str):
    for p in (DAILY / f"{sym}_daily.csv", D3Y / f"{sym}.csv"):
        if p.exists():
            try:
                return pd.read_csv(p, dtype={"date": str}).sort_values("date").reset_index(drop=True)
            except Exception:
                return None
    return None


def episodes(d: pd.DataFrame) -> list:
    prev = d["Close"].shift(1)
    locked = (d["High"] == d["Close"]) & (d["High"] >= prev * 1.029)
    idx = [i for i, v in enumerate(locked) if v and d["date"].iloc[i] >= START]
    runs, cur = [], []
    for i in idx:
        if cur and i == cur[-1] + 1:
            cur.append(i)
        else:
            if cur:
                runs.append(cur)
            cur = [i]
    if cur:
        runs.append(cur)
    return runs


def calibrate(sym: str, d: pd.DataFrame, runs: list, con: sqlite3.Connection) -> dict | None:
    sys.path.insert(0, str(BASE / "Tse2" / "orderflow"))
    try:
        import tsetmc as api
        m = api.resolve(sym)
        code = m["ins_code"] if m else None
    except Exception:
        code = None
    if not code:
        return None
    r14 = rsi(d["Close"].astype(float))
    close = d["Close"].astype(float)
    ma20 = close.rolling(20, min_periods=10).mean()
    sd20 = close.rolling(20, min_periods=10).std()
    bb = (close - (ma20 - 2 * sd20)) / ((4 * sd20).replace(0, np.nan))

    rows = []
    for r in runs[:60]:
        i = r[0]
        if i == 0:
            continue
        try:
            de = gdate(d["date"].iloc[i])
        except Exception:
            continue
        q = con.execute("SELECT cel, tot FROM day_ceiling WHERE ins_code=? AND d_even=?", (code, de)).fetchone()
        if not q or not q[1]:
            continue
        rows.append({"len": len(r), "rsi_b": r14.iloc[i - 1], "bb_b": bb.iloc[i - 1],
                     "ceil_b": (q[0] or 0) / 1e9, "share": (q[0] or 0) / q[1] * 100})
    if not rows:
        return None
    t = pd.DataFrame(rows).dropna(subset=["rsi_b", "bb_b"])
    if not len(t):
        return None
    pos = (t.len >= MIN_LEN).to_numpy()
    if pos.sum() == 0:
        return None

    best = None
    for rl in RSI_MINS:
        for rh in RSI_MAXS:
            for bm in BB_MAXS:
                for cm in CEIL_MAXS:
                    for sm in SHARE_MINS:
                        sig = ((t.rsi_b >= rl) & (t.rsi_b <= rh) & (t.bb_b < bm) &
                               (t.ceil_b < cm) & (t.share >= sm)).to_numpy()
                        tp = int((sig & pos).sum())
                        fp = int((sig & ~pos).sum())
                        fn = int((~sig & pos).sum())
                        if tp == 0:
                            continue
                        prec = tp / (tp + fp) * 100
                        rec = tp / (tp + fn) * 100
                        cand = (1 if prec >= 75 else 0, round(rec, 1), round(prec, 1), tp, fp, fn, rl, rh, bm, cm, sm)
                        if best is None or cand > best:
                            best = cand
    if best is None:
        return None
    _, rec, prec, tp, fp, fn, rl, rh, bm, cm, sm = best
    return {"symbol": sym, "long_episodes": int(pos.sum()), "episodes": len(t),
            "rsi_min": rl, "rsi_max": rh, "bb_max": bm,
            "ceil_max_b": None if cm >= 1e9 else cm, "share_min": sm,
            "signals": tp + fp, "hit": tp, "false": fp, "missed": fn,
            "precision": prec, "recall": rec,
            "passes_75": bool(prec >= 75)}


def main() -> int:
    con = sqlite3.connect(TAPE)
    precompute(con)
    files = sorted(DAILY.glob("*_daily.csv")) + sorted(D3Y.glob("*.csv"))
    syms, seen = [], set()
    for f in files:
        s = f.name[:-len("_daily.csv")] if f.name.endswith("_daily.csv") else f.stem
        if s not in seen:
            seen.add(s)
            syms.append(s)
    print(f"symbols to scan: {len(syms)}", flush=True)

    ok_rows, wl_rows = [], []
    for k in range(0, len(syms), BATCH):
        batch = syms[k:k + BATCH]
        for sym in batch:
            d = load(sym)
            if d is None or len(d) < 80:
                wl_rows.append({"symbol": sym, "reason": "داده ناکافی"})
                continue
            runs = episodes(d)
            long_runs = [r for r in runs if len(r) >= MIN_LEN]
            if not long_runs:
                wl_rows.append({"symbol": sym, "reason": "بدون صف ۵ روزه",
                                "episodes": len(runs)})
                continue
            row = calibrate(sym, d, runs, con)
            if row:
                ok_rows.append(row)
            else:
                wl_rows.append({"symbol": sym, "reason": "کالیبراسیون ناموفق",
                                "episodes": len(runs)})
        # checkpoint every batch
        if ok_rows:
            pd.DataFrame(ok_rows).to_excel(OUT_OK, index=False)
        if wl_rows:
            pd.DataFrame(wl_rows).to_excel(OUT_WL, index=False)
        print(f"batch {k//BATCH + 1}: ok={len(ok_rows)} wl={len(wl_rows)}", flush=True)
    con.close()
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
