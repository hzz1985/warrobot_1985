"""Per-symbol pattern workbook for every listed symbol.

One row per symbol, built from state/bt_ab_rows.csv (1405 -> today):

  Stage A — behaviour      : days, lock rate, close_pos (all / on locks),
                             up-day ratio, average and win rate of the trade
  Stage B — technical      : RSI median (all / on locks), share of MACD-positive
                             days, BB median, and the lock probability lift of
                             each tool for THIS symbol
  Stage C — best pattern   : the combination with the highest lock probability
                             for this symbol, with its sample size and return

Writes C:\\Users\\Administrator\\Desktop\\symbol_patterns.xlsx
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
ROWS = BASE / "WarRobot" / "state" / "bt_ab_rows.csv"
STAGE_A = BASE / "WarRobot" / "state" / "stage_a.csv"
OUT = Path(r"C:\Users\Administrator\Desktop\symbol_patterns.xlsx")


def pct(x) -> float:
    return round(float(x) * 100, 1)


def one(g: pd.DataFrame) -> dict:
    locks = g[g.locked_today == 1]
    best = ("-", None, None)
    cands = [
        ("RSI>=60 & MACD+", (g.rsi >= 60) & (g.macd_pos == 1)),
        ("RSI<60 & MACD+", (g.rsi < 60) & (g.macd_pos == 1)),
        ("BB>=0.8 & MACD+", (g.bb >= 0.8) & (g.macd_pos == 1)),
        ("RSI<60 & MACD+ & BB>=0.8", (g.rsi < 60) & (g.macd_pos == 1) & (g.bb >= 0.8)),
        ("MACD-", (g.macd_pos == 0)),
    ]
    for name, m in cands:
        s = g[m]
        if len(s) >= 10:
            score = (s.locked_next.mean(), s.ret.mean())
            if best[1] is None or score[0] > best[1]:
                best = (name, score[0], score[1])
    return {
        "نماد": g.symbol.iloc[0],
        "روزهای بررسی": len(g),
        "روزهای قفل": int(g.locked_today.sum()),
        "نرخ قفل %": pct(g.locked_today.mean()),
        "میانه موقعیت بسته‌شدن": round(float(g.close_pos.median()), 2) if g.close_pos.notna().any() else None,
        "میانه موقعیت در روز قفل": round(float(locks.close_pos.median()), 2) if len(locks) and locks.close_pos.notna().any() else None,
        "سهم روزهای صعودی %": pct((g.ret > 0).mean()),
        "میانه بازده معامله %": round(float(g.ret.median()), 2),
        "میانگین بازده %": round(float(g.ret.mean()), 2),
        "میانگین بازده روز قفل %": round(float(locks.ret.mean()), 2) if len(locks) else None,
        "RSI میانه": round(float(g.rsi.median()), 1),
        "RSI در روز قفل": round(float(locks.rsi.median()), 1) if len(locks) else None,
        "سهم روزهای MACD مثبت %": pct((g.macd_pos == 1).mean()),
        "BB میانه": round(float(g.bb.median()), 2),
        "P(قفل بعدی) %": round(float(g.locked_next.mean()) * 100, 1),
        "قفل بعد از RSI>=60 %": round(float(g[g.rsi >= 60].locked_next.mean()) * 100, 1) if (g.rsi >= 60).any() else None,
        "قفل بعد از RSI<60 %": round(float(g[g.rsi < 60].locked_next.mean()) * 100, 1) if (g.rsi < 60).any() else None,
        "قفل بعد از MACD+ %": round(float(g[g.macd_pos == 1].locked_next.mean()) * 100, 1) if (g.macd_pos == 1).any() else None,
        "قفل بعد از MACD- %": round(float(g[g.macd_pos == 0].locked_next.mean()) * 100, 1) if (g.macd_pos == 0).any() else None,
        "قفل بعد از BB>=0.8 %": round(float(g[g.bb >= 0.8].locked_next.mean()) * 100, 1) if (g.bb >= 0.8).any() else None,
        "بهترین الگو": best[0],
        "P(قفل | الگو) %": round(best[1] * 100, 1) if best[1] is not None else None,
        "بازده | الگو %": round(best[2], 2) if best[2] is not None else None,
    }


def main() -> int:
    t = pd.read_csv(ROWS)
    print("rows:", len(t), "symbols:", t.symbol.nunique())
    per = pd.DataFrame([one(g) for _, g in t.groupby("symbol")])

    if STAGE_A.exists():
        try:
            a = pd.read_csv(STAGE_A)
            ren = {"symbol": "نماد", "ceiling_value_median_b": "هسته سقف (میلیارد ریال)",
                   "ceiling_share_median_pct": "سهم سقف از کل %",
                   "turnover_median_b": "میانه ارزش معاملات (میلیارد ریال)",
                   "lock_freq_pct": "نرخ قفل % (مرحله A)"}
            a = a.rename(columns={k: v for k, v in ren.items() if k in a.columns})
            per = per.merge(a[[c for c in ren.values() if c in a.columns]], on="نماد", how="left")
        except Exception as exc:
            print("stage_a merge skipped:", str(exc)[:60])

    per = per.sort_values("نرخ قفل %", ascending=False)

    method = pd.DataFrame({
        "مرحله": ["A", "A", "A", "B", "B", "B", "C"],
        "پارامتر": ["نرخ قفل", "موقعیت بسته‌شدن", "سهم روزهای صعودی",
                    "RSI(14)", "MACD (هیستوگرام)", "باند بولینگر",
                    "الگوی برتر هر سهم"],
        "توضیح": ["درصد روزهایی که سهم روی سقف بسته",
                  "میانگین (بسته−کف)/(سقف−کف) در روز",
                  "درصد روزهای با بازده مثبت",
                  "قدرت نسبی خریدار در ۱۴ روز",
                  "مومنتوم کوتاه‌مدت در برابر بلندمدت",
                  "موقعیت قیمت در باند نوسان ۲۰ روزه",
                  "ترکیبی که برای همان سهم بیشترین احتمال قفل بعدی را دارد"],
    })

    with pd.ExcelWriter(OUT, engine="openpyxl") as xw:
        per.to_excel(xw, sheet_name="الگوی هر سهم", index=False)
        method.to_excel(xw, sheet_name="روش", index=False)
    print("written:", OUT, "| symbols:", len(per))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
