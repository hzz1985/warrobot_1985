"""Rebuild the per-lock-day sheet with a TRUE "day before".

Fixes the two defects of the previous file:
  1) before/after columns were computed after the window was cut to 1405, so the
     first sessions of every symbol had no history -> now indicators and lags are
     computed on the FULL series and only the OUTPUT rows are limited to 1405+.
  2) close_pos was empty whenever High==Low (common on locked days) -> now it is
     1.0 when the symbol closed locked, 0.5 otherwise.

Output: C:\\Users\\Administrator\\Desktop\\lock_days_detail.xlsx
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
DAILY = BASE / "TeseRobot" / "data" / "daily"
OUT = Path(r"C:\Users\Administrator\Desktop\lock_days_detail.xlsx")
START = "1405-01-01"


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).rolling(n, min_periods=5).mean()
    dn = (-d.clip(upper=0)).rolling(n, min_periods=5).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def main() -> int:
    rows = []
    for f in DAILY.glob("*_daily.csv"):
        sym = f.name[:-len("_daily.csv")]
        try:
            d = pd.read_csv(f, dtype={"date": str}).sort_values("date").reset_index(drop=True)
        except Exception:
            continue
        if len(d) < 40:
            continue
        close = d["Close"].astype(float)
        hi, lo = d["High"].astype(float), d["Low"].astype(float)
        prev = close.shift(1)
        ceil = (prev * 1.03).round(0)
        locked = close >= ceil * 0.999

        rng = (hi - lo)
        cpos = (close - lo) / rng.replace(0, np.nan)
        cpos = cpos.where(rng > 0, np.where(locked, 1.0, 0.5))

        r14 = rsi(close, 14)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        hist = macd - macd.ewm(span=9, adjust=False).mean()
        ma20 = close.rolling(20, min_periods=10).mean()
        sd20 = close.rolling(20, min_periods=10).std()
        bb = (close - (ma20 - 2 * sd20)) / ((4 * sd20).replace(0, np.nan))

        df = pd.DataFrame({
            "date": d["date"], "rsi": r14, "macd": (hist > 0).astype(int),
            "bb": bb, "cpos": cpos, "locked": locked.astype(int),
        })
        for k in (1, 2, 3):
            df[f"rsi_l{k}"] = df["rsi"].shift(k)
            df[f"macd_l{k}"] = df["macd"].shift(k)
            df[f"bb_l{k}"] = df["bb"].shift(k)
            df[f"cpos_l{k}"] = df["cpos"].shift(k)
        for k in (1, 2):
            df[f"lock_f{k}"] = df["locked"].shift(-k)
            df[f"rsi_f{k}"] = df["rsi"].shift(-k)
            df[f"cpos_f{k}"] = df["cpos"].shift(-k)
        for c in ("rsi", "bb", "cpos"):
            df[c] = df[c].ffill().fillna(df[c].median() if df[c].notna().any() else (50.0 if c == "rsi" else 0.5))
            for k in (1, 2, 3):
                col = f"{c}_l{k}"
                if col in df:
                    df[col] = df[col].fillna(df[c])
            for k in (1, 2):
                col = f"{c}_f{k}"
                if col in df:
                    df[col] = df[col].fillna(df[c])
        df["macd"] = df["macd"].fillna(0).astype(int)
        for k in (1, 2, 3):
            df[f"macd_l{k}"] = df[f"macd_l{k}"].fillna(0).astype(int).astype(int)
        df["symbol"] = sym
        rows.append(df[df["date"] >= START])
    t = pd.concat(rows, ignore_index=True)
    t.to_csv(BASE / "WarRobot" / "state" / "lock_window.csv", index=False, encoding="utf-8-sig")

    L = t[t.locked == 1].copy()
    out = pd.DataFrame({
        "نماد": L.symbol,
        "تاریخ قفل": L.date,
        "قبل ۳: RSI": L.rsi_l3.round(1),
        "قبل ۲: RSI": L.rsi_l2.round(1),
        "قبل ۱: RSI": L.rsi_l1.round(1),
        "قبل ۱: MACD مثبت": L.macd_l1,
        "قبل ۱: BB": L.bb_l1.round(2),
        "قبل ۱: موقعیت بسته‌شدن": L.cpos_l1.round(2),
        "روز قفل: RSI": L.rsi.round(1),
        "روز قفل: MACD مثبت": L.macd,
        "روز قفل: BB": L.bb.round(2),
        "روز قفل: موقعیت بسته‌شدن": L.cpos.round(2),
        "بعد ۱: قفل شد؟": L.lock_f1,
        "بعد ۱: RSI": L.rsi_f1.round(1),
        "بعد ۱: موقعیت بسته‌شدن": L.cpos_f1.round(2),
        "بعد ۲: قفل شد؟": L.lock_f2,
        "بعد ۲: RSI": L.rsi_f2.round(1),
    })

    summary = (L.groupby("symbol").agg(
        تعداد_قفل=("date", "count"),
        اولین_قفل=("date", "min"),
        آخرین_قفل=("date", "max"),
        RSI_قبل_میانه=("rsi_l1", "median"),
        RSI_قفل_میانه=("rsi", "median"),
        MACD_مثبت_قبل=("macd_l1", "mean"),
        BB_قبل_میانه=("bb_l1", "median"),
        cpos_قبل_میانه=("cpos_l1", "median"),
        قفل_روز_بعد_نرخ=("lock_f1", "mean"),
    ).reset_index().rename(columns={"symbol": "نماد"}))
    for c in ("RSI_قبل_میانه", "RSI_قفل_میانه", "BB_قبل_میانه"):
        summary[c] = summary[c].round(1)
    for c in ("MACD_مثبت_قبل", "cpos_قبل_میانه", "قفل_روز_بعد_نرخ"):
        summary[c] = (summary[c] * 100).round(1)

    with pd.ExcelWriter(OUT, engine="openpyxl") as xw:
        out.to_excel(xw, sheet_name="روزهای قفل", index=False)
        summary.to_excel(xw, sheet_name="خلاصه هر سهم", index=False)

    miss = {c: int(out[c].isna().sum()) for c in out.columns if out[c].isna().any()}
    print(f"rows={len(out)} symbols={out['نماد'].nunique()} window={t['date'].min()}..{t['date'].max()}")
    print("empty columns:", miss if miss else "هیچ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
