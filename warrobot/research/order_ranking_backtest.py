"""Does the ORDER of the candidates matter?

The scan is a binary filter: `locked = High == Close and Close >= PrevClose*1.029`.
Among the locked names there is no ranking today (file order). Before building a
probability model we test the cheap question: does a smarter order beat the file
order?

Rules compared (same entries, same exits, same equal sizing):
    file_order    current behaviour (first six in file order)
    volume        highest traded volume first
    turnover      highest traded value first
    prior_locks   names that locked more often in the previous 20 sessions
    random        control (seeded)

Exit: +25% target, -3% stop, 15 sessions. Entry: next session open.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from collections import defaultdict, deque
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
DAILY = BASE / "TeseRobot" / "data" / "daily"
D3Y = BASE / "Tse2" / "orderflow" / "data" / "daily3y"
OUT = BASE / "WarRobot" / "research" / "order_ranking_result.json"

TP, SL, HOLD, MAX_PICK = 0.25, -0.03, 15, 6
FEE = 0.005 * 2   # round-trip commission + tax approximation


def load(sym_file: Path) -> pd.DataFrame | None:
    try:
        d = pd.read_csv(sym_file, dtype={"date": str})
    except Exception:
        return None
    if len(d) < 30:
        return None
    d = d.dropna(subset=["Close", "Volume"])
    d = d[d["Close"] > 0].sort_values("date").reset_index(drop=True)
    return d if len(d) >= 30 else None


def signals(sym: str, d: pd.DataFrame) -> list[dict]:
    out = []
    prev_close = d["Close"].shift(1)
    locked = (d["High"] == d["Close"]) & (d["Close"] >= prev_close * 1.029)
    d = d.assign(prev=prev_close, locked=locked)
    # rolling count of locks in the previous 20 sessions (prior_locks)
    d["prior_locks"] = locked.shift(1).rolling(20).sum()
    for i in range(20, len(d) - HOLD):
        if not bool(d.loc[i, "locked"]):
            continue
        row = d.loc[i]
        out.append({"date": str(row["date"]), "symbol": sym,
                    "volume": float(row["Volume"]),
                    "turnover": float(row["Volume"]) * float(row["Close"]),
                    "prior_locks": float(d.loc[i, "prior_locks"] or 0),
                    "i": i})
    return out


def simulate(d: pd.DataFrame, i: int) -> float:
    entry_i = i + 1
    entry = float(d.loc[entry_i, "Open"]) or float(d.loc[entry_i, "Close"])
    if entry <= 0:
        return 0.0
    for k in range(entry_i, min(entry_i + HOLD, len(d))):
        lo, hi = float(d.loc[k, "Low"]), float(d.loc[k, "High"])
        if lo / entry - 1 <= SL:
            return SL - FEE
        if hi / entry - 1 >= TP:
            return TP - FEE
    exit_i = min(entry_i + HOLD - 1, len(d) - 1)
    return float(d.loc[exit_i, "Close"]) / entry - 1 - FEE


def main() -> int:
    files = {}
    for f in sorted(D3Y.glob("*.csv")):
        files[f.stem] = f
    for f in sorted(DAILY.glob("*_daily.csv")):
        files.setdefault(f.name[:-len("_daily.csv")], f)

    # per symbol: data + all signal days
    data: dict[str, pd.DataFrame] = {}
    by_day: dict[str, list[dict]] = defaultdict(list)
    for sym, fp in files.items():
        d = load(fp)
        if d is None:
            continue
        sig = signals(sym, d)
        if sig:
            data[sym] = d
            for s in sig:
                by_day[s["date"]].append(s)
    print(f"symbols={len(data)} signal-days={len(by_day)} "
          f"signals={sum(len(v) for v in by_day.values())}")

    rules = ["file_order", "volume", "turnover", "prior_locks", "random"]
    rnd = random.Random(42)
    results = {r: [] for r in rules}

    for day, rows in sorted(by_day.items()):
        if len(rows) < 1:
            continue
        orders = {
            "file_order": rows[:MAX_PICK],
            "volume": sorted(rows, key=lambda x: -x["volume"])[:MAX_PICK],
            "turnover": sorted(rows, key=lambda x: -x["turnover"])[:MAX_PICK],
            "prior_locks": sorted(rows, key=lambda x: (-x["prior_locks"], -x["turnover"]))[:MAX_PICK],
            "random": rnd.sample(rows, min(MAX_PICK, len(rows))),
        }
        for rule, picked in orders.items():
            for s in picked:
                d = data[s["symbol"]]
                results[rule].append(simulate(d, s["i"]))

    # paired test: same day, volume-selected vs file-selected
    paired = []
    for day, rows in sorted(by_day.items()):
        if len(rows) < 2:
            continue
        f_sel = rows[:MAX_PICK]
        v_sel = sorted(rows, key=lambda x: -x["volume"])[:MAX_PICK]
        f_ret = [simulate(data[s["symbol"]], s["i"]) for s in f_sel]
        v_ret = [simulate(data[s["symbol"]], s["i"]) for s in v_sel]
        if f_ret and v_ret:
            paired.append(statistics.fmean(v_ret) - statistics.fmean(f_ret))
    paired_stats = None
    if len(paired) > 2:
        import math
        mu = statistics.fmean(paired)
        sd = statistics.pstdev(paired) or 1e-9
        t = mu / (sd / math.sqrt(len(paired)))
        paired_stats = {"days": len(paired), "mean_diff_pct": round(100 * mu, 3),
                        "t_stat": round(t, 2)}

    summary = {}
    for rule, rets in results.items():
        if not rets:
            continue
        wins = sum(1 for r in rets if r > 0)
        summary[rule] = {"trades": len(rets),
                         "win_pct": round(100 * wins / len(rets), 2),
                         "avg_ret_pct": round(100 * statistics.fmean(rets), 3),
                         "median_pct": round(100 * statistics.median(rets), 3),
                         "total_sum_pct": round(100 * sum(rets), 1)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"signals": sum(len(v) for v in by_day.values()),
                               "days": len(by_day), "rules": summary,
                               "paired_volume_vs_file": paired_stats},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    for rule, s in summary.items():
        print(f"{rule:12s} trades={s['trades']:6d} win={s['win_pct']:6.2f}% "
              f"avg={s['avg_ret_pct']:+.3f}% median={s['median_pct']:+.3f}% "
              f"sum={s['total_sum_pct']:+.1f}%")
    print("saved:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
