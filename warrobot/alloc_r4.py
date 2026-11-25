"""Round 4: stress tests for the trend-filter rule.

A) robustness : MA length 100/150/200/250, with a switch cost.
B) out-of-sample : the MA length is chosen on 1401-1402 and evaluated on 1403-1405.
C) gold shock : gold returns are replaced by 0% and by -10%/yr (stocks unchanged)
                to see whether the edge survives a gold bear market.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
OUT = BASE / "state" / "alloc_r4.json"
HOLD = 20
COST = 0.005          # 0.5% each time the allocation changes


def load() -> pd.DataFrame:
    import sys
    sys.path.insert(0, str(BASE))
    from alloc_r3 import load as load_r3
    return load_r3()


def simulate(df: pd.DataFrame, ma: int, start: str, end: str,
             gold_mode: str = "real", cost: float = COST) -> dict:
    days = list(df.index)
    px = (1 + df["stock"]).cumprod()
    ma_s = px.rolling(ma, min_periods=int(ma * 0.6)).mean()
    eq, curve, prev = 1.0, [], None
    for k in range(int(ma * 0.7), len(days) - HOLD, HOLD):
        d0, d1 = days[k], days[min(k + HOLD - 1, len(days) - 1)]
        if d0 < start or d1 >= end:
            continue
        win = df.iloc[k:k + HOLD].copy()
        if gold_mode == "flat":
            win["gold"] = 0.0
        elif gold_mode == "bear":
            win["gold"] = (1 - 0.10) ** (1 / 252) - 1
        up = px.iloc[k - 1] > ma_s.iloc[k - 1]
        alloc = "mixed" if up else "all_gold"
        if alloc == "mixed":
            r = float((1 + 0.5 * win["stock"] + 0.5 * win["gold"]).prod() - 1)
        else:
            r = float((1 + win["gold"]).prod() - 1)
        if prev is not None and prev != alloc:
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
    df.index = df.index.astype(str)
    res = {}

    print("A) مقاومت به طول میانگین (۱۴۰۱–۱۴۰۵، با کارمزد ۰٫۵٪ در هر تغییر):")
    for ma in (100, 150, 200, 250):
        r = simulate(df, ma, "1401-01-01", "1406-01-01")
        res[f"ma{ma}"] = r
        print(f"   MA{ma}: {r['ret']:+9.1f}% · افت {r['dd']:5.1f}% · نسبت {r['ratio']:7.1f}")

    print("\nB) آزمون خارج از نمونه (انتخاب MA روی ۱۴۰۱–۱۴۰۲، آزمون ۱۴۰۳–۱۴۰۵):")
    pick = {}
    for ma in (100, 150, 200, 250):
        pick[ma] = simulate(df, ma, "1401-01-01", "1403-01-01")["ret"]
    best = max(pick, key=pick.get)
    print(f"   انتخاب آموزش: MA{best} ({pick[best]:+.1f}%)")
    test = simulate(df, best, "1403-01-01", "1406-01-01")
    base_st = simulate(df, best, "1403-01-01", "1406-01-01", gold_mode="flat")
    res["oos"] = {"chosen": best, "train": pick, "test": test, "test_gold_flat": base_st}
    print(f"   آزمون: {test['ret']:+.1f}% · افت {test['dd']:.1f}%")

    print("\nC) سناریوی رکود طلا (۱۴۰۳–۱۴۰۵):")
    for mode, label in (("real", "طلا واقعی"), ("flat", "طلا صفر"), ("bear", "طلا −۱۰٪ در سال")):
        r = simulate(df, best, "1403-01-01", "1406-01-01", gold_mode=mode)
        res[f"gold_{mode}"] = r
        print(f"   {label:16s}: {r['ret']:+9.1f}% · افت {r['dd']:5.1f}% · نسبت {r['ratio']:7.1f}")

    # stocks-only reference for the same window
    st = float((1 + df.loc[(df.index >= "1403-01-01") & (df.index < "1406-01-01"), "stock"]).prod() - 1) * 100
    res["stocks_only"] = round(st, 1)
    print(f"\n   مرجع: سهم‌تنها در همین پنجره {st:+.1f}%")

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
