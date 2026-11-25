"""Stage A (microstructure) + Stage B (technical) per stock.

A) For each symbol: how much money trades AT the ceiling on locked days
   ("cost of lock"), what share of the day that is, and how often it locks.
B) For each symbol: RSI(14), MACD histogram and Bollinger position, and the
   conditional probability of locking given each indicator bucket.

Output: two compact tables (printed + JSON) for the web app.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import jdatetime
import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
DAILY = BASE / "TeseRobot" / "data" / "daily"
TAPE = BASE / "Tse2" / "orderflow" / "tape_1405.db"
OUT = BASE / "WarRobot" / "state" / "stage_ab.json"
SYMBOLS = 700


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    d = series.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def load_symbols():
    out = []
    for f in DAILY.glob("*_daily.csv"):
        try:
            d = pd.read_csv(f, dtype={"date": str})
        except Exception:
            continue
        if len(d) < 60:
            continue
        d = d.sort_values("date").reset_index(drop=True)
        d["in_window"] = d["date"] >= "1405-01-01"
        if int(d["in_window"].sum()) < 30:
            continue
        prev = d["Close"].shift(1)
        ceil = (prev * 1.03).round(0)
        d["locked"] = d["Close"] >= ceil * 0.999
        if d.loc[d["in_window"], "locked"].sum() < 3:
            continue
        out.append((f.name[:-len("_daily.csv")], d))
    out.sort(key=lambda x: -x[1]["locked"].sum())
    return out[:SYMBOLS]


def pressure(d) -> dict:
    """Buyer/seller pressure profile from daily bars (no client data needed)."""
    close = d["Close"].astype(float)
    hi, lo = d["High"].astype(float), d["Low"].astype(float)
    rng = (hi - lo).replace(0, np.nan)
    close_pos = ((close - lo) / rng).clip(0, 1)
    w = d[d["in_window"]]
    cp = close_pos[d["in_window"]]
    return {
        "close_pos_median": round(float(cp.median()), 3),
        "close_pos_on_locks": round(float(cp[w["locked"]].median()), 3) if w["locked"].any() else None,
        "up_days_ratio": round(float((close.diff() > 0)[d["in_window"]].mean()), 3),
        "lock_freq_pct": round(float(w["locked"].mean() * 100), 1),
    }


def gregorian(dates):
    out = {}
    for x in dates:
        y, m, dd = (int(v) for v in x.split("-"))
        out[x] = int(jdatetime.date(y, m, dd).togregorian().strftime("%Y%m%d"))
    return out


def stage_a(sym, d, con):
    inst = con.execute("SELECT 1").fetchone()  # keep connection warm
    from tsetmc import resolve
    meta = resolve(sym)
    if not meta:
        return None
    ic = meta["ins_code"]
    g = gregorian(d["date"].tolist())
    rows = con.execute("""
        SELECT d_even, COUNT(*), SUM(price*qty)
        FROM tape WHERE ins_code=? AND canceled=0 GROUP BY d_even
    """, (ic,)).fetchall()
    if not rows:
        return None
    total = {r[0]: (r[1], r[2]) for r in rows}
    ceil_by_g = {}
    prev = d["Close"].shift(1)
    for i, x in enumerate(d["date"]):
        if pd.isna(prev.iloc[i]):
            continue
        ceil_by_g[g[x]] = float(round(prev.iloc[i] * 1.03))
    at_ceiling = {}
    for de in total:
        c = ceil_by_g.get(de)
        if not c:
            continue
        v = con.execute("""SELECT COUNT(*), SUM(price*qty) FROM tape
                           WHERE ins_code=? AND d_even=? AND canceled=0 AND price=?""",
                        (ic, de, c)).fetchone()
        if v and v[1]:
            at_ceiling[de] = v
    lock_days = [g[x] for x, l in zip(d["date"], d["locked"]) if l and g.get(x) in total]
    if not lock_days:
        return None
    cv = [at_ceiling[de][1] for de in lock_days if de in at_ceiling and at_ceiling[de][1]]
    tv = [total[de][1] for de in lock_days]
    if not cv:
        return None
    return {
        "symbol": sym,
        "lock_days": len(lock_days),
        "ceiling_value_median_b": round(float(np.median(cv)) / 1e9, 2),
        "ceiling_share_median_pct": round(float(np.median([c / t * 100 for c, t in zip(cv, tv)])), 1),
        "turnover_median_b": round(float(np.median(tv)) / 1e9, 1),
    }


def stage_b(sym, d):
    close = d["Close"].astype(float)
    r14 = rsi(close, 14)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    ma20 = close.rolling(20).mean()
    sd20 = close.rolling(20).std()
    bb_pos = (close - (ma20 - 2 * sd20)) / ((4 * sd20).replace(0, np.nan))

    df = pd.DataFrame({"locked": d["locked"], "rsi": r14, "hist": hist, "bb": bb_pos,
                       "in_window": d["in_window"]}).dropna()
    df = df[df["in_window"]]
    if len(df) < 8:
        return None
    near_lock = df["rsi"] >= 60
    out = {
        "symbol": sym,
        "rsi_locked_median": round(float(df[df.locked]["rsi"].median()), 1),
        "rsi_all_median": round(float(df["rsi"].median()), 1),
        "p_lock_rsi60plus": round(float(df[df.rsi >= 60]["locked"].mean() * 100), 1),
        "p_lock_rsi_below60": round(float(df[df.rsi < 60]["locked"].mean() * 100), 1),
        "p_lock_macd_pos": round(float(df[df["hist"] > 0]["locked"].mean() * 100), 1),
        "p_lock_macd_neg": round(float(df[df["hist"] <= 0]["locked"].mean() * 100), 1),
        "p_lock_bb_top": round(float(df[df.bb >= 0.8]["locked"].mean() * 100), 1),
        "p_lock_bb_mid": round(float(df[df.bb < 0.8]["locked"].mean() * 100), 1),
        "n": int(len(df)),
    }
    return out


def main() -> int:
    con = sqlite3.connect(TAPE)
    syms = load_symbols()
    a_rows, b_rows = [], []
    for sym, d in syms:
        try:
            a = stage_a(sym, d, con)
            if a:
                a.update(pressure(d))
                a_rows.append(a)
        except Exception:
            pass
        try:
            b = stage_b(sym, d)
            if b:
                b_rows.append(b)
        except Exception as exc:
            print("B error", sym, str(exc)[:60])
    con.close()
    A = pd.DataFrame(a_rows)
    B = pd.DataFrame(b_rows)
    print("=== مرحله A: ریزمعاملات و عرضهٔ سقف ===")
    print(A.sort_values("ceiling_value_median_b", ascending=False).to_string(index=False))
    print("\n=== مرحله B: تکنیکال ===")
    if len(B):
        print(B.sort_values("rsi_locked_median", ascending=False).to_string(index=False))
    else:
        print("(no rows)")
    OUT.write_text(json.dumps({"stage_a": a_rows, "stage_b": b_rows}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    pd.DataFrame(a_rows).to_csv(OUT.parent / "stage_a.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(b_rows).to_csv(OUT.parent / "stage_b.csv", index=False, encoding="utf-8-sig")
    if len(A) and len(B):
        merged = A.merge(B, on="symbol", how="outer")
        merged.to_csv(OUT.parent / "symbol_profiles.csv", index=False, encoding="utf-8-sig")
        print("profiles:", len(merged))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
