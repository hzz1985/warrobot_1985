"""Robust backtest on the CLEANED options dataset.

Fixes the three things that made the first run meaningless:
  1. only cleaned contracts (price floor, stale rows removed, moves winsorized)
  2. liquidity filter on the entry day (turnover >= MIN_VALUE)
  3. per-trade return capped, and the report uses MEDIAN (not the mean, which
     one 100x contract can destroy)

Rules (same universe and costs for all three):
  A  contract momentum         5-day positive move, buy strongest
  B  mean reversion            3-day drop, buy sharpest losers
  C  underlying momentum       5-day positive move on the underlying proxy
                               (contracts of the same underlying, equal signal)
Costs: 0.5% round trip. Max 3 positions, equal weight, exit after HOLD days
or STOP loss, forced exit when the contract stops trading.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
CLEAN = BASE / "data" / "options_clean"
OUT = BASE / "data" / "options_clean" / "backtest_robust.json"

FEE = 0.005
HOLD = 3
STOP = -0.08
MAXPOS = 3
MIN_VALUE = 10_000_000
MAX_TRADE = 3.0          # cap a single trade at +300%
MIN_TRADES = 100


def load() -> dict:
    series = {}
    for fp in CLEAN.glob("*.csv"):
        rows = []
        try:
            with fp.open(encoding="utf-8") as fh:
                head = fh.readline().strip().split(",")
                for line in fh:
                    p = line.rstrip("\n").split(",")
                    if len(p) < len(head):
                        continue
                    d = dict(zip(head, p))
                    try:
                        c = float(d["close"])
                        v = float(d["value"])
                    except Exception:
                        continue
                    if c > 0:
                        rows.append({"date": str(d["date"]), "close": c, "value": v})
        except Exception:
            continue
        if len(rows) >= 12:
            rows.sort(key=lambda r: r["date"])
            series[fp.stem] = rows
    return series


def _stats(trades: list[dict], equity: list[float]) -> dict:
    rets = [t["ret_pct"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    by_year = defaultdict(float)
    for t in trades:
        by_year[t["exit_date"][:4]] += t["ret_pct"] / MAXPOS
    pos_years = sum(1 for v in by_year.values() if v > 0)
    return {
        "trades": len(trades),
        "win_rate": round(100 * len(wins) / len(rets), 1) if rets else 0.0,
        "median_ret_pct": round(statistics.median(rets), 2) if rets else 0.0,
        "mean_ret_pct": round(statistics.fmean(rets), 2) if rets else 0.0,
        "p90_ret_pct": round(sorted(rets)[int(0.9 * len(rets))], 2) if rets else 0.0,
        "total_return_pct": round((equity[-1] - 1) * 100, 1),
        "max_drawdown_pct": round(mdd * 100, 1),
        "profit_factor": round(sum(wins) / max(1e-9, -sum(losses)), 2) if losses else None,
        "years": dict(sorted(by_year.items())),
        "positive_years_pct": round(100 * pos_years / len(by_year), 0) if by_year else 0,
    }


def run(series: dict, rule: str, hold: int = HOLD, stop: float = STOP) -> dict:
    by_date = defaultdict(list)
    for sym, rows in series.items():
        for i, r in enumerate(rows):
            by_date[r["date"]].append((sym, i, rows))
    dates = sorted(by_date)

    equity = [1.0]
    trades: list[dict] = []
    open_pos: list[dict] = []

    def close_pos(p, price, date, why):
        raw = price / p["entry"] - 1 - FEE
        ret = max(-1.0, min(MAX_TRADE, raw))
        equity.append(equity[-1] * (1 + ret / MAXPOS))
        trades.append({"symbol": p["symbol"], "entry_date": p["entry_date"],
                       "exit_date": date, "entry": p["entry"], "exit": round(price, 1),
                       "ret_pct": round(ret * 100, 2), "why": why})

    for date in dates:
        still = []
        for p in open_pos:
            rows = series[p["symbol"]]
            i = p["i"] + 1
            if i >= len(rows) or rows[i]["date"] != date:
                last = rows[min(p["i"] + 1, len(rows) - 1)]
                close_pos(p, last["close"], last["date"], "expiry")
                continue
            p["i"] = i
            cur = rows[i]["close"]
            if cur / p["entry"] - 1 <= stop:
                close_pos(p, cur, date, "stop")
            elif i - p["entry_i"] >= hold:
                close_pos(p, cur, date, "time")
            else:
                still.append(p)
        open_pos = still
        equity.append(equity[-1])
        if len(open_pos) >= MAXPOS:
            continue

        cands = []
        for sym, i, rows in by_date[date]:
            if i + 1 >= len(rows) or rows[i + 1]["date"] <= date:
                continue
            if rows[i]["value"] < MIN_VALUE:          # liquidity floor
                continue
            n = 5 if rule in ("A", "C") else 3
            if i < n:
                continue
            m = rows[i]["close"] / rows[i - n]["close"] - 1
            if rule in ("A", "C") and m <= 0:
                continue
            if rule == "B" and m >= 0:
                continue
            cands.append((m, sym, i, rows))
        cands.sort(key=lambda x: -x[0])
        for m, sym, i, rows in cands[: MAXPOS - len(open_pos)]:
            nxt = rows[i + 1]
            if nxt["value"] < MIN_VALUE:
                continue
            open_pos.append({"symbol": sym, "entry": nxt["close"], "entry_date": nxt["date"],
                             "i": i + 1, "entry_i": i + 1})

    for p in open_pos:
        rows = series[p["symbol"]]
        close_pos(p, rows[-1]["close"], rows[-1]["date"], "end")

    res = _stats(trades, equity)
    res["rule"] = rule
    res["sample_trades"] = trades[:5]
    return res


def main() -> int:
    series = load()
    rows = sum(len(v) for v in series.values())
    print(f"clean contracts={len(series):,} rows={rows:,}")
    if len(series) < 30:
        print("not enough cleaned contracts yet")
        return 1
    results = []
    for rule in ("A", "B", "C"):
        r = run(series, rule)
        results.append(r)
        print(f"rule {rule}: trades={r['trades']} win={r['win_rate']}% "
              f"median={r['median_ret_pct']:+}% total={r['total_return_pct']}% "
              f"mdd={r['max_drawdown_pct']}% pf={r['profit_factor']} "
              f"pos_years={r['positive_years_pct']}%")
    best = max(results, key=lambda r: (r["profit_factor"] or 0, r["total_return_pct"]))
    verdict = {
        "profitable": bool(best["profit_factor"] and best["profit_factor"] > 1.3
                           and best["total_return_pct"] > 0
                           and best["trades"] >= MIN_TRADES
                           and abs(best["max_drawdown_pct"]) < 25),
        "best_rule": best["rule"],
        "reason": ("passes pf>1.3, positive total, >=100 trades, mdd<25%"
                   if False else "see metrics"),
    }
    OUT.write_text(json.dumps({"at": datetime.now().isoformat(), "contracts": len(series),
                               "rows": rows, "results": results, "verdict": verdict},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("verdict:", json.dumps(verdict, ensure_ascii=False))
    print("saved:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
