"""BFomul — the combined microstructure + technical signal, per symbol.

BFomul(tape, technicals) — currently calibrated for تاپیکو only.

Definition (all four must hold on the session BEFORE the queue starts):

    1) RSI(14)            between 35 and 75      (not overbought, not broken)
    2) Bollinger position <  0.85                (price not at the upper band)
    3) ceiling turnover   <  600 B rial           (little traded at the ceiling)
    4) ceiling share      >= 95%                  (almost everything trades at the ceiling)

Calibration (تاپیکو, 1405 -> today, TP15/SL4/15 sessions, 2% fees):
    4 signals · avg +8.25% · total +33% · win 75% · 3/3 long queues caught
    (the 3-condition variant gives +9.20% with 5 signals and 2 false positives)

The 4-condition version is the chosen one: fewest false signals.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import jdatetime
import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
TAPE = BASE / "Tse2" / "orderflow" / "tape_1405.db"
STATE = BASE / "WarRobot" / "state" / "bfomul.json"

SPEC = {
    "name": "BFomul",
    "version": 1,
    "symbols": ["تاپیکو"],
    "conditions": {
        "rsi_min": 35, "rsi_max": 75,
        "bb_max": 0.85,
        "ceiling_value_max_b": 600,
        "ceiling_share_min": 95,
    },
    "calibration": {
        "window": "1405-01-01..today",
        "signals": 4, "avg_return_pct": 8.25, "total_return_pct": 33.0,
        "win_rate_pct": 75, "long_queue_coverage": "3/3", "false_positives": 1,
    },
}


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    d = series.diff()
    up = d.clip(lower=0).rolling(n, min_periods=5).mean()
    dn = (-d.clip(upper=0)).rolling(n, min_periods=5).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def evaluate(symbol: str = "تاپیکو", lookback_date: str | None = None) -> dict:
    """Return the BFomul verdict for `lookback_date` (default: last session)."""
    fp = BASE / "TeseRobot" / "data" / "daily" / f"{symbol}_daily.csv"
    if not fp.exists():
        fp = BASE / "Tse2" / "orderflow" / "data" / "daily3y" / f"{symbol}.csv"
    d = pd.read_csv(fp, dtype={"date": str}).sort_values("date").reset_index(drop=True)
    close = d["Close"].astype(float)
    r14 = rsi(close)
    ma20 = close.rolling(20, min_periods=10).mean()
    sd20 = close.rolling(20, min_periods=10).std()
    bb = (close - (ma20 - 2 * sd20)) / ((4 * sd20).replace(0, np.nan))

    idx = len(d) - 1 if lookback_date is None else int(d.index[d["date"] == lookback_date][0])
    date = d["date"].iloc[idx]
    r, b = float(r14.iloc[idx]), float(bb.iloc[idx])

    # ceiling turnover from the tape (trades at the day's top price)
    code = None
    try:
        import sys
        sys.path.insert(0, str(BASE / "Tse2" / "orderflow"))
        import tsetmc as api
        m = api.resolve(symbol)
        code = m["ins_code"] if m else None
    except Exception:
        code = None
    ceil_b, share = None, None
    if code:
        y, mo, dd = (int(x) for x in str(date).split("-"))
        de = int(jdatetime.date(y, mo, dd).togregorian().strftime("%Y%m%d"))
        con = sqlite3.connect(TAPE)
        q = con.execute("SELECT SUM(price*qty), MAX(price) FROM tape WHERE ins_code=? AND d_even=? AND canceled=0",
                        (code, de)).fetchone()
        if q and q[0]:
            mx = q[1] or 0
            c = con.execute("SELECT SUM(price*qty) FROM tape WHERE ins_code=? AND d_even=? AND canceled=0 AND price>=?",
                            (code, de, mx * 0.999)).fetchone()[0] or 0
            ceil_b = round(c / 1e9, 2)
            share = round(c / q[0] * 100, 1)
        con.close()

    c = SPEC["conditions"]
    checks = {
        "rsi": (c["rsi_min"] <= r <= c["rsi_max"]) if not np.isnan(r) else False,
        "bb": (b < c["bb_max"]) if not np.isnan(b) else False,
        "ceiling_value": (ceil_b is not None and ceil_b < c["ceiling_value_max_b"]),
        "ceiling_share": (share is not None and share >= c["ceiling_share_min"]),
    }
    return {"symbol": symbol, "date": str(date), "rsi": round(r, 1), "bb": round(b, 2),
            "ceiling_value_b": ceil_b, "ceiling_share_pct": share,
            "checks": checks, "signal": bool(all(checks.values()))}


def main() -> int:
    res = evaluate("تاپیکو")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    STATE.write_text(json.dumps({"spec": SPEC, "latest": res}, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
