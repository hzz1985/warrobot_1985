"""Daily allocation advice (shadow mode — never trades).

Reads the freshest local prices and prints/records the rule's recommendation:

    index > MA200                  -> 50% stocks + 50% gold fund
    index < MA200 and gold > MA200 -> 100% gold fund
    index < MA200 and gold < MA200 -> 100% fixed-income fund

Writes state/alloc_advice.json (latest) and appends to state/alloc_history.csv
so the advice can be checked against what actually happened later.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE))
OUT = BASE / "state" / "alloc_advice.json"
HIST = BASE / "state" / "alloc_history.csv"
D3Y = BASE.parent / "Tse2" / "orderflow" / "data" / "daily3y"
MA = 200


def stock_index() -> pd.Series:
    """Equal-weight index; the fresher daily folder wins over the 3-year one."""
    rets = []
    fresh = BASE.parent / "TeseRobot" / "data" / "daily"
    files = list(fresh.glob("*_daily.csv")) or list(D3Y.glob("*.csv"))
    for f in files:
        try:
            d = pd.read_csv(f, dtype={"date": str})
        except Exception:
            continue
        if len(d) < 120:
            continue
        name = f.name[:-len("_daily.csv")] if f.name.endswith("_daily.csv") else f.stem
        rets.append(d.tail(300).set_index("date")["Close"].astype(float).pct_change().rename(name))
    r = pd.concat(rets, axis=1).mean(axis=1).sort_index()
    return (1 + r.fillna(0)).cumprod()


def fund_px(name: str) -> pd.Series:
    fp = BASE / "data" / "funds" / f"{name}.csv"
    d = pd.read_csv(fp, dtype={"date": str})
    import jdatetime

    def to_j(g: str) -> str:
        try:
            y, m, dd = (int(x) for x in str(g).split("-"))
            return jdatetime.date.fromgregorian(year=y, month=m, day=dd).strftime("%Y-%m-%d")
        except Exception:
            return ""

    d["j"] = d["date"].map(to_j)
    s = d[d["j"] != ""].set_index("j")["close"].astype(float)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    r = s.pct_change()
    return s[(r.abs() < 0.40) | r.isna()]


def main() -> int:
    idx = stock_index()
    gold = fund_px("طلا")
    fixed = fund_px("اعتماد")

    ma_idx = idx.rolling(MA, min_periods=120).mean().iloc[-1]
    px_idx = idx.iloc[-1]
    ma_gold = gold.rolling(MA, min_periods=120).mean().iloc[-1]
    px_gold = gold.iloc[-1]

    idx_up = bool(px_idx > ma_idx)
    gold_up = bool(px_gold > ma_gold)

    if idx_up:
        alloc, reason = {"stocks": 50, "gold": 50, "fixed": 0}, "شاخص بالای میانگین ۲۰۰ روزه"
    elif gold_up:
        alloc, reason = {"stocks": 0, "gold": 100, "fixed": 0}, "شاخص زیر میانگین، طلا در روند صعودی"
    else:
        alloc, reason = {"stocks": 0, "gold": 0, "fixed": 100}, "هر دو نزولی — درآمد ثابت"

    payload = {
        "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "session": str(idx.index[-1]),
        "index": {"px": round(float(px_idx), 2), "ma200": round(float(ma_idx), 2),
                  "above": idx_up, "gap_pct": round(float((px_idx / ma_idx - 1) * 100), 2)},
        "gold": {"px": round(float(px_gold), 0), "ma200": round(float(ma_gold), 0),
                 "above": gold_up, "gap_pct": round(float((px_gold / ma_gold - 1) * 100), 2)},
        "allocation": alloc,
        "reason": reason,
        "mode": "shadow (بدون معامله)",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    row = {"date": payload["session"], "index_above": int(idx_up), "gold_above": int(gold_up),
           "stocks_pct": alloc["stocks"], "gold_pct": alloc["gold"], "fixed_pct": alloc["fixed"],
           "reason": reason}
    new = not HIST.exists()
    with HIST.open("a", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)

    print(f"session {payload['session']} · index {payload['index']['gap_pct']:+.2f}% vs MA200 "
          f"· gold {payload['gold']['gap_pct']:+.2f}% vs MA200")
    print(f"توصیه: سهم {alloc['stocks']}% · طلا {alloc['gold']}% · درآمد ثابت {alloc['fixed']}% · {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
