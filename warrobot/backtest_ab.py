"""Three-stage pattern validation, 1405 -> today, all symbols.

Stage A : per-symbol microstructure/behaviour profile (close_pos, lock rate,
          up-day ratio) measured on the same window.
Stage B : per-symbol technical state (RSI14, MACD histogram, Bollinger position).
Stage C : do A and B actually predict (a) locking the next session and
          (b) the realised TP15/SL4 return? Measured per bucket and per symbol.

Output: state/bt_ab_rows.csv (one row per symbol-day) and a printed summary.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
DAILY = BASE / "TeseRobot" / "data" / "daily"
OUT = BASE / "WarRobot" / "state" / "bt_ab_rows.csv"
TP, SL, HOLD = 0.15, 0.04, 15


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def main() -> int:
    rows = []
    files = list(DAILY.glob("*_daily.csv"))
    for f in files:
        sym = f.name[:-len("_daily.csv")]
        try:
            d = pd.read_csv(f, dtype={"date": str}).sort_values("date").reset_index(drop=True)
        except Exception:
            continue
        if len(d) < 60:
            continue
        close = d["Close"].astype(float)
        prev = close.shift(1)
        ceil = (prev * 1.03).round(0)
        locked = close >= ceil * 0.999
        rng = (d["High"].astype(float) - d["Low"].astype(float)).replace(0, np.nan)
        cpos = ((close - d["Low"].astype(float)) / rng).clip(0, 1)

        r14 = rsi(close, 14)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        hist = macd - macd.ewm(span=9, adjust=False).mean()
        ma20 = close.rolling(20).mean()
        sd20 = close.rolling(20).std()
        bb = (close - (ma20 - 2 * sd20)) / ((4 * sd20).replace(0, np.nan))

        for i in range(len(d) - 1):
            if d["date"].iloc[i] < "1405-01-01":
                continue
            if pd.isna(r14.iloc[i]) or pd.isna(hist.iloc[i]) or pd.isna(bb.iloc[i]):
                continue
            nxt = d.iloc[i + 1]
            entry = float(nxt["Open"])
            if entry <= 0:
                continue
            ret, why = None, "time"
            for k in range(HOLD):
                j = i + 1 + k
                if j >= len(d):
                    break
                if float(d["High"].iloc[j]) >= entry * (1 + TP):
                    ret = TP * 100 - 2.0
                    why = "tp"
                    break
                if float(d["Low"].iloc[j]) <= entry * (1 - SL):
                    ret = -SL * 100 - 2.0
                    why = "sl"
                    break
                ret = (float(d["Close"].iloc[j]) / entry - 1) * 100 - 2.0
                why = "time"
            if ret is None:
                continue
            rows.append({
                "symbol": sym, "date": d["date"].iloc[i],
                "rsi": round(float(r14.iloc[i]), 1),
                "macd_pos": int(hist.iloc[i] > 0),
                "bb": round(float(bb.iloc[i]), 2),
                "close_pos": round(float(cpos.iloc[i]), 2) if not pd.isna(cpos.iloc[i]) else None,
                "locked_today": int(locked.iloc[i]),
                "locked_next": int(locked.iloc[i + 1]),
                "ret": round(ret, 2), "exit": why,
            })
    t = pd.DataFrame(rows)
    t.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"rows={len(t)} symbols={t.symbol.nunique()} -> {OUT.name}")

    in_band = t[t.locked_today == 1]
    print(f"\nستون: کاندیدهای قفل‌شده (locked_today) = {len(in_band)}")
    for label, mask in (("RSI>=60", in_band.rsi >= 60), ("RSI<60", in_band.rsi < 60),
                        ("MACD+", in_band.macd_pos == 1), ("MACD-", in_band.macd_pos == 0),
                        ("BB>=0.8", in_band.bb >= 0.8), ("BB<0.8", in_band.bb < 0.8)):
        s = in_band[mask]
        if len(s):
            print(f"  {label:8s} n={len(s):6d} · قفل فردا {s.locked_next.mean()*100:5.1f}% · بازده {s.ret.mean():+5.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
