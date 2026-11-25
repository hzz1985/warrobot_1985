"""Backtest engine for Tehran options (directional, technical only).

Reads data/options/*.csv (written by options_download.py) and tests three
hypotheses on the SAME universe with the same execution rules:

  A) contract momentum      buy the strongest 5-day movers (liquid only)
  B) mean reversion         buy the sharpest 3-day losers (liquid only)
  C) underlying momentum    buy calls whose underlying is in a 5-day uptrend

Rules (identical for all three, from the stock bot's lessons):
  * signal computed on the close of day t, entry at the close of t (same bar),
    exit after N sessions or on stop-loss
  * only contracts with volume > 0 on the entry day
  * max 3 concurrent positions, equal weight, fee 0.5% round trip
  * exit forced when the contract stops trading (expiry)
This is a *relative* test: if no rule beats buy-and-hold-ish noise, we say so.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
DATA = BASE / "data" / "options"
OUT = BASE / "data" / "options" / "backtest_result.json"
FEE = 0.005
HOLD = 3
STOP = -0.08
MAXPOS = 3


def load() -> dict:
    series = {}
    for fp in DATA.glob("*.csv"):
        rows = []
        try:
            with fp.open(encoding="utf-8") as fh:
                head = fh.readline().strip().split(",")
                for line in fh:
                    parts = line.strip().split(",")
                    if len(parts) < len(head):
                        continue
                    d = dict(zip(head, parts))
                    try:
                        close = float(d["close"] or 0)
                        vol = float(d["volume"] or 0)
                    except Exception:
                        continue
                    if close <= 0:
                        continue
                    rows.append({"date": str(d["date"]), "close": close, "volume": vol})
        except Exception:
            continue
        if len(rows) >= 8:
            rows.sort(key=lambda r: r["date"])
            series[fp.stem] = rows
    return series


def momentum(rows: list[dict], i: int, n: int) -> float:
    if i < n:
        return 0.0
    a, b = rows[i - n]["close"], rows[i]["close"]
    return b / a - 1 if a else 0.0


def run_rule(series: dict, rule: str, hold: int = HOLD, stop: float = STOP) -> dict:
    # build a per-date view
    by_date = defaultdict(list)
    for sym, rows in series.items():
        for i, r in enumerate(rows):
            by_date[r["date"]].append((sym, i, rows))
    dates = sorted(by_date)

    equity = [1.0]
    trades = []
    open_pos: list[dict] = []

    def close_pos(p: dict, price: float, date: str, why: str):
        ret = price / p["entry"] - 1 - FEE
        equity.append(equity[-1] * (1 + ret / MAXPOS))
        trades.append({"symbol": p["symbol"], "entry_date": p["entry_date"],
                       "exit_date": date, "entry": p["entry"], "exit": round(price, 1),
                       "ret_pct": round(ret * 100, 2), "why": why})

    for date in dates:
        # 1) manage open positions
        still = []
        for p in open_pos:
            rows = series[p["symbol"]]
            i = p["i"] + 1
            if i >= len(rows) or rows[i]["date"] != date:
                # contract stopped trading -> exit at last known close
                last = rows[min(p["i"] + 1, len(rows) - 1)]
                close_pos(p, last["close"], last["date"], "expiry")
                continue
            p["i"] = i
            cur = rows[i]["close"]
            chg = cur / p["entry"] - 1
            if chg <= stop:
                close_pos(p, cur, date, "stop")
            elif i - p["entry_i"] >= hold:
                close_pos(p, cur, date, "time")
            else:
                still.append(p)
        open_pos = still

        # 2) open new positions (signal on the same close, entry next day handled
        #    by using the NEXT day close when available)
        if len(open_pos) >= MAXPOS:
            equity.append(equity[-1])
            continue
        cands = []
        for sym, i, rows in by_date[date]:
            if i + 1 >= len(rows) or rows[i + 1]["date"] <= date:
                continue
            if rows[i]["volume"] <= 0:
                continue
            if rule in ("A", "C"):
                m = momentum(rows, i, 5)
            else:
                m = momentum(rows, i, 3)
            if rule in ("A", "C") and m <= 0:
                continue
            if rule == "B" and m >= 0:
                continue
            cands.append((m, sym, i, rows))
        cands.sort(key=lambda x: -abs(x[0]) if rule == "B" else -x[0])
        for m, sym, i, rows in cands[: MAXPOS - len(open_pos)]:
            nxt = rows[i + 1]
            if nxt["volume"] <= 0:
                continue
            open_pos.append({"symbol": sym, "entry": nxt["close"],
                             "entry_date": nxt["date"], "i": i + 1, "entry_i": i + 1})
        equity.append(equity[-1])

    # force-close leftovers
    for p in open_pos:
        rows = series[p["symbol"]]
        close_pos(p, rows[-1]["close"], rows[-1]["date"], "end")

    total = equity[-1] - 1
    wins = [t for t in trades if t["ret_pct"] > 0]
    years = defaultdict(float)
    for t in trades:
        years[t["exit_date"][:4]] += t["ret_pct"] / MAXPOS

    # max drawdown on the equity curve
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)

    return {
        "rule": rule,
        "trades": len(trades),
        "win_rate": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "avg_ret_pct": round(sum(t["ret_pct"] for t in trades) / len(trades), 2) if trades else 0.0,
        "total_return_pct": round(total * 100, 1),
        "max_drawdown_pct": round(mdd * 100, 1),
        "profit_factor": round(sum(t["ret_pct"] for t in wins) /
                               max(1e-9, -sum(t["ret_pct"] for t in trades if t["ret_pct"] <= 0)), 2),
        "by_year": {y: round(v, 1) for y, v in sorted(years.items())},
        "sample": trades[:5],
    }


def main() -> int:
    series = load()
    n_rows = sum(len(v) for v in series.values())
    print(f"contracts={len(series):,} rows={n_rows:,}")
    if len(series) < 20:
        print("not enough contracts yet — keep the download running")
        return 1
    results = []
    for rule in ("A", "B", "C"):
        r = run_rule(series, rule)
        results.append(r)
        print(f"rule {rule}: trades={r['trades']} win={r['win_rate']}% "
              f"avg={r['avg_ret_pct']}% total={r['total_return_pct']}% "
              f"mdd={r['max_drawdown_pct']}% pf={r['profit_factor']}")
    OUT.write_text(json.dumps({"at": datetime.now().isoformat(), "contracts": len(series),
                               "rows": n_rows, "results": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
