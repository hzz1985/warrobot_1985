"""Round 7: the three remaining tests for the trend-guard rule (real fund prices).

a) gold shock     : the gold fund's returns are replaced by 0% and by -10%/yr
b) per year       : is it consistent, or carried by one or two years?
c) switching cost : 0.5% (base), 1.5% and 2.5% per change (fees + tax drag)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE))
from alloc_funds import load  # noqa: E402

OUT = BASE / "state" / "alloc_r7.json"
HOLD = 20
MA = 200


def sim(df: pd.DataFrame, rule: str, start: str, end: str,
        gold_mode: str = "real", cost: float = 0.005) -> dict:
    days = list(df.index)
    eq, curve, prev = 1.0, [], None
    for k in range(MA, len(days) - HOLD, HOLD):
        d0, d1 = days[k], days[min(k + HOLD - 1, len(days) - 1)]
        if d0 < start or d1 >= end:
            continue
        win = df.iloc[k:k + HOLD].copy()
        if gold_mode == "flat":
            win["gold"] = 0.0
        elif gold_mode == "bear":
            win["gold"] = (1 - 0.10) ** (1 / 252) - 1
        st_up = df["px_s"].iloc[k - 1] > df["ma_s"].iloc[k - 1]
        gd_up = df["px_g"].iloc[k - 1] > df["ma_g"].iloc[k - 1]

        if rule == "stocks":
            r = float((1 + win["stock"]).prod() - 1)
            alloc = "stock"
        elif rule == "trend_guard":
            if st_up:
                r = float((1 + 0.5 * win["stock"] + 0.5 * win["gold"]).prod() - 1)
                alloc = "half"
            elif gd_up:
                r = float((1 + win["gold"]).prod() - 1)
                alloc = "gold"
            else:
                r = float((1 + win["fixed"]).prod() - 1)
                alloc = "fixed"
        else:
            r, alloc = 0.0, "none"

        if rule == "trend_guard" and prev is not None and prev != alloc:
            r -= cost
        prev = alloc
        eq *= 1 + r
        curve.append(eq)
    s = pd.Series(curve) if curve else pd.Series([1.0])
    dd = float((1 - s / s.cummax()).max() * 100) if len(s) > 1 else 0.0
    return {"ret": round((eq - 1) * 100, 1), "dd": round(dd, 1),
            "ratio": round((eq - 1) * 100 / max(dd, 0.1), 1)}


def main() -> int:
    df = load()
    res = {}

    print("a) سناریوی طلا (۱۴۰۳–۱۴۰۵):")
    for mode, label in (("real", "طلا واقعی"), ("flat", "طلا صفر"), ("bear", "طلا −۱۰٪/سال")):
        t = sim(df, "trend_guard", "1403-01-01", "1406-01-01", gold_mode=mode)
        s = sim(df, "stocks", "1403-01-01", "1406-01-01", gold_mode=mode)
        res[f"gold_{mode}"] = {"guard": t, "stocks": s}
        print(f"   {label:14s} نگهبان {t['ret']:+9.1f}% (افت {t['dd']:.1f}%) | سهم‌تنها {s['ret']:+9.1f}%")

    print("\nb) سال‌به‌سال (قیمت واقعی، کارمزد ۰٫۵٪):")
    print(f"   {'سال':6s} {'نگهبان':>10s} {'سهم':>10s} {'طلا':>10s}")
    years = {}
    for y in ("1400", "1401", "1402", "1403", "1404", "1405"):
        a, b = f"{y}-01-01", f"{int(y)+1}-01-01"
        t = sim(df, "trend_guard", a, b)
        s = sim(df, "stocks", a, b)
        g = float((1 + df.loc[(df.index >= a) & (df.index < b), "gold"]).prod() - 1) * 100
        years[y] = {"guard": t["ret"], "stocks": s["ret"], "gold": round(g, 1)}
        print(f"   {y:6s} {t['ret']:+9.1f}% {s['ret']:+9.1f}% {g:+9.1f}%")
    res["years"] = years

    print("\nc) هزینهٔ جابه‌جایی (۱۴۰۱–۱۴۰۵):")
    for cost, label in ((0.005, "۰٫۵٪"), (0.015, "۱٫۵٪"), (0.025, "۲٫۵٪")):
        r = sim(df, "trend_guard", "1401-01-01", "1406-01-01", cost=cost)
        res[f"cost_{int(cost*1000)}"] = r
        print(f"   هزینه {label}: {r['ret']:+9.1f}% · افت {r['dd']:.1f}% · نسبت {r['ratio']:.1f}")

    guard_wins = sum(1 for y in years if years[y]["guard"] > years[y]["stocks"])
    res["years_guard_beats_stocks"] = guard_wins
    print(f"\nنتیجه: نگهبان در {guard_wins} سال از {len(years)} سال از سهم‌تنها بهتر بود")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
