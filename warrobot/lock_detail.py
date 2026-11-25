"""Per-lock-day detail workbook: behaviour and technicals BEFORE and AFTER the lock.

Source: state/bt_ab_rows.csv (one row per symbol-day, 1405 -> today).
For every day a symbol closed locked, the row carries:

    before  : RSI / MACD / Bollinger / close_pos of the 3 previous sessions
    lock day: the same values ON the lock day + the trade return
    after   : the next 3 sessions (locked?, RSI, return)

Writes C:\\Users\\Administrator\\Desktop\\lock_days_detail.xlsx
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
ROWS = BASE / "WarRobot" / "state" / "bt_ab_rows.csv"
OUT = Path(r"C:\Users\Administrator\Desktop\lock_days_detail.xlsx")


def main() -> int:
    t = pd.read_csv(ROWS)
    t = t.sort_values(["symbol", "date"]).reset_index(drop=True)
    g = t.groupby("symbol", sort=False)

    for k in (1, 2, 3):
        t[f"rsi_lag{k}"] = g["rsi"].shift(k)
        t[f"macd_lag{k}"] = g["macd_pos"].shift(k)
        t[f"bb_lag{k}"] = g["bb"].shift(k)
        t[f"cpos_lag{k}"] = g["close_pos"].shift(k)
        t[f"rsi_lead{k}"] = g["rsi"].shift(-k)
        t[f"locked_lead{k}"] = g["locked_next"].shift(-(k - 1))
        t[f"ret_lead{k}"] = g["ret"].shift(-k)

    locks = t[t.locked_today == 1].copy()
    out = pd.DataFrame({
        "نماد": locks.symbol,
        "تاریخ قفل": locks.date,
        # ---- before the lock
        "RSI سه روز قبل": locks.rsi_lag3,
        "RSI دو روز قبل": locks.rsi_lag2,
        "RSI روز قبل": locks.rsi_lag1,
        "MACD روز قبل": locks.macd_lag1,
        "BB روز قبل": locks.bb_lag1,
        "موقعیت بسته‌شدن روز قبل": locks.cpos_lag1,
        "موقعیت بسته‌شدن ۳ روز قبل": locks.cpos_lag3,
        # ---- on the lock day
        "RSI روز قفل": locks.rsi,
        "MACD روز قفل": locks.macd_pos,
        "BB روز قفل": locks.bb,
        "موقعیت بسته‌شدن روز قفل": locks.close_pos,
        "بازده معامله %": locks.ret,
        # ---- after the lock
        "قفل روز بعد": locks.locked_lead2,
        "قفل دو روز بعد": locks.locked_lead3,
        "RSI روز بعد": locks.rsi_lead1,
        "RSI دو روز بعد": locks.rsi_lead2,
        "بازده روز بعد %": locks.ret_lead1,
        "بازده دو روز بعد %": locks.ret_lead2,
    })

    summary = (locks.groupby("symbol")
               .agg(تعداد_روز_قفل=("date", "count"),
                    اولین_قفل=("date", "min"),
                    آخرین_قفل=("date", "max"),
                    RSI_میانه=("rsi", "median"),
                    RSI_روز_قبل_میانه=("rsi_lag1", "median"),
                    MACD_مثبت_قبل=("macd_lag1", "mean"),
                    BB_میانه=("bb", "median"),
                    cpos_میانه=("close_pos", "median"),
                    بازده_میانه=("ret", "median"),
                    بازده_میانگین=("ret", "mean"))
               .reset_index()
               .rename(columns={"symbol": "نماد"}))
    for c in ("RSI_میانه", "RSI_روز_قبل_میانه", "BB_میانه"):
        summary[c] = summary[c].round(1)
    for c in ("بازده_میانه", "بازده_میانگین", "MACD_مثبت_قبل"):
        summary[c] = summary[c].round(2)

    method = pd.DataFrame({
        "بخش": ["قبل از قفل", "قبل از قفل", "قبل از قفل", "روز قفل", "روز قفل", "بعد از قفل", "بعد از قفل"],
        "پارامتر": ["RSI ۳/۲/۱ روز قبل", "MACD روز قبل", "BB و موقعیت بسته‌شدن روز قبل",
                    "RSI/MACD/BB روز قفل", "موقعیت بسته‌شدن روز قفل", "قفل روز بعد و دو روز بعد",
                    "RSI و بازده روز بعد/دو روز بعد"],
        "کاربرد": ["آیا قبل از قفل، RSI در حال بالا رفتن بود؟",
                   "آیا مومنتوم قبل از قفل مثبت شده بود؟",
                   "آیا قیمت به سقف باند نزدیک شده بود؟",
                   "وضعیت ابزارها در لحظهٔ قفل",
                   "قدرت خریدار در همان روز",
                   "آیا صف دوام آورد؟",
                   "روند پس از قفل"],
    })

    with pd.ExcelWriter(OUT, engine="openpyxl") as xw:
        out.to_excel(xw, sheet_name="روزهای قفل", index=False)
        summary.to_excel(xw, sheet_name="خلاصه هر سهم", index=False)
        method.to_excel(xw, sheet_name="روش", index=False)

    print(f"lock rows={len(out)} symbols={out['نماد'].nunique()} -> {OUT}")
    print(f"window: {t.date.min()} .. {t.date.max()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
