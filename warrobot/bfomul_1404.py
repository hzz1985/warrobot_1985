"""1) BFormulAll.txt  — every symbol's own BFomul, written for Notepad.
2) 1404BFomul.xlsx — each symbol's BFomul re-tested on 1404 (out of sample).

Important: the tape (order-level) data exists only for 1405, so on 1404 the two
microstructure conditions are approximated from the daily bar:
    ceiling turnover  ~ Volume * Close   (on a locked day everything trades there)
    ceiling share     ~ 100% when High == Close, else 0
That keeps conditions 3 and 4 measurable out of sample, but it is an
approximation and is flagged in the output.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
OK = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\data\bfomul_by_symbol.xlsx")
TXT = Path(r"C:\Users\Administrator\Desktop\BFormulAll.txt")
OUT = Path(r"C:\Users\Administrator\Desktop\1404BFomul.xlsx")
DAILY = BASE / "TeseRobot" / "data" / "daily"
D3Y = BASE / "Tse2" / "orderflow" / "data" / "daily3y"
TP, SL, HOLD = 0.15, 0.04, 15


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


def write_txt(df: pd.DataFrame) -> None:
    lines = ["BFormulAll — فرمول BFomul هر نماد", "=" * 60, ""]
    for _, r in df.sort_values("symbol").iterrows():
        ceil = "بی‌نهایت" if pd.isna(r.get("ceil_max_b")) else f"{r['ceil_max_b']:,.0f}"
        lines += [
            f"نماد: {r['symbol']}",
            f"  RSI: بین {r['rsi_min']} و {r['rsi_max']}",
            f"  بولینگر < {r['bb_max']}",
            f"  ارزش معاملات سقف < {ceil} میلیارد ریال",
            f"  سهم ارزش سقف ≥ {r['share_min']}٪",
            f"  کالیبراسیون ۱۴۰۵: صف بلند {r['long_episodes']} · سیگنال {r['signals']} · "
            f"دقت {r['precision']}٪ · پوشش {r['recall']}٪",
            "",
        ]
    TXT.write_text("\n".join(lines), encoding="utf-8")
    print("written:", TXT)


def trade(d: pd.DataFrame, i: int):
    j0 = i + 1
    if j0 >= len(d):
        return None
    entry = float(d["Open"].iloc[j0])
    if entry <= 0:
        return None
    last = None
    for k in range(HOLD):
        j = j0 + k
        if j >= len(d):
            break
        last = float(d["Close"].iloc[j])
        if float(d["High"].iloc[j]) >= entry * (1 + TP):
            return TP * 100 - 2.0
        if float(d["Low"].iloc[j]) <= entry * (1 - SL):
            return -SL * 100 - 2.0
    return None if last is None else (last / entry - 1) * 100 - 2.0


def test_1404(r: pd.Series) -> dict | None:
    sym = r["symbol"]
    d = load(sym)
    if d is None or len(d) < 80:
        return None
    d = d[(d["date"] >= "1404-01-01") & (d["date"] <= "1404-12-30")].reset_index(drop=True)
    if len(d) < 40:
        return None
    close = d["Close"].astype(float)
    r14 = rsi(close)
    ma20 = close.rolling(20, min_periods=10).mean()
    sd20 = close.rolling(20, min_periods=10).std()
    bb = (close - (ma20 - 2 * sd20)) / ((4 * sd20).replace(0, np.nan))
    prev = close.shift(1)
    locked = (d["High"] == d["Close"]) & (d["High"] >= prev * 1.029)
    turn = d["Volume"].astype(float) * close

    ceil_max = 1e9 if pd.isna(r.get("ceil_max_b")) else float(r["ceil_max_b"])
    sigs, rets = 0, []
    for i in range(1, len(d)):
        if pd.isna(r14.iloc[i - 1]) or pd.isna(bb.iloc[i - 1]):
            continue
        ok = (r["rsi_min"] <= r14.iloc[i - 1] <= r["rsi_max"] and
              bb.iloc[i - 1] < r["bb_max"] and
              turn.iloc[i] / 1e9 < ceil_max and
              (100.0 if locked.iloc[i] else 0.0) >= r["share_min"])
        if not ok:
            continue
        sigs += 1
        t = trade(d, i)
        if t is not None:
            rets.append(t)
    if not sigs:
        return {"symbol": sym, "signals_1404": 0, "avg_return": None, "total": None, "win": None}
    return {"symbol": sym, "signals_1404": sigs, "trades": len(rets),
            "avg_return": round(float(np.mean(rets)), 2) if rets else None,
            "total": round(float(np.sum(rets)), 1) if rets else None,
            "win": round(float(np.mean([1 if x > 0 else 0 for x in rets]) * 100), 0) if rets else None}


def main() -> int:
    df = pd.read_excel(OK)
    write_txt(df)
    rows = []
    for _, r in df.iterrows():
        try:
            res = test_1404(r)
            if res:
                rows.append(res)
        except Exception:
            continue
    out = pd.DataFrame(rows)
    out.to_excel(OUT, index=False)
    tested = out[out.signals_1404 > 0]
    print(f"symbols={len(df)} tested_1404={len(out)} with_signals={len(tested)} -> {OUT.name}")
    if len(tested):
        print("میانگین بازده:", round(tested.avg_return.mean(), 2), "% | مجموع:",
              round(tested.total.sum(), 1), "%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
