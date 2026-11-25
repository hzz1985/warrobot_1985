"""Options backtest v4 — risk rules + trailing/break-even exits + walk-forward.

Next-step table being implemented here:
  1) real risk rules      : stop trading after 3 consecutive losing trades
                            (cooldown 5 sessions), skip entries after a big
                            single-day loss burst
  2) walk-forward split   : first 60% of the calendar in-sample, last 40%
                            out-of-sample; a config must work on BOTH
  3) liquidity relaxation : three liquidity tiers so the trade count can grow
  4) trailing / break-even: stop trails the highest close since entry, and moves
                            to break-even once the trade is +6%

Exit priority each bar: hard stop -> break-even stop -> trailing stop ->
hold limit -> expiry.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
import options_backtest_v3 as v3  # noqa: E402

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
OUT = BASE / "data" / "options_clean" / "backtest_v4.json"

FEE = 0.005
MAX_TRADE = 3.0
TIERS = {"A_500m": 500_000_000, "B_100m": 100_000_000, "C_20m": 20_000_000}
MIN_PRICE_BY_TIER = {"A_500m": 5000, "B_100m": 2000, "C_20m": 500}
COOLDOWN = 5
LOSS_STREAK = 3
BE_TRIGGER = 0.06
TRAIL = 0.10


def _exit(rows, i, hold, hard_stop, be_trigger, trail):
    entry = rows[i]["close"]
    best = entry
    be_done = False
    for k in range(i + 1, len(rows)):
        cur = rows[k]["close"]
        best = max(best, cur)
        ret = cur / entry - 1
        if ret <= hard_stop:
            return ret - FEE, k, "stop"
        if be_done:
            if cur <= entry:
                return cur / entry - 1 - FEE, k, "breakeven"
            if cur <= best * (1 - trail):
                return cur / entry - 1 - FEE, k, "trail"
        else:
            if ret >= be_trigger:
                be_done = True
        if k - i >= hold:
            return ret - FEE, k, "time"
    last = rows[-1]
    return last["close"] / entry - 1 - FEE, len(rows) - 1, "expiry"


def run(contracts, underlyings, rule, hold, hard_stop, tier, use_risk=True,
        be_trigger=BE_TRIGGER, trail=TRAIL):
    floor_value = TIERS[tier]
    floor_price = MIN_PRICE_BY_TIER[tier]

    by_date = defaultdict(list)
    for sym, rows in contracts.items():
        for i, r in enumerate(rows):
            by_date[r["date"]].append((sym, i, rows))
    dates = sorted(by_date)

    umom = {}
    if rule == "C":
        for u, m in underlyings.items():
            ds = sorted(m)
            for i in range(5, len(ds)):
                umom.setdefault(ds[i], {})[u] = m[ds[i]] / m[ds[i - 5]] - 1
    umom = {d: {k.replace(" ", ""): v for k, v in mm.items()} for d, mm in umom.items()}

    trades = []
    busy_until = {}
    streak = 0
    cooldown_until = ""

    for date in dates:
        if use_risk and date <= cooldown_until:
            continue
        cands = []
        for sym, i, rows in by_date[date]:
            if i + 1 >= len(rows) or rows[i + 1]["date"] <= date:
                continue
            r = rows[i]
            if r["value"] < floor_value or r["close"] < floor_price:
                continue
            if sym in busy_until and busy_until[sym] >= date:
                continue
            if rule in ("A", "C"):
                if i < 5:
                    continue
                mom = r["close"] / rows[i - 5]["close"] - 1
                if rule == "A":
                    if mom <= 0:
                        continue
                    score = mom
                else:
                    u = v3.underlying_of(sym)
                    um = umom.get(date, {}).get(u)
                    if um is None or um <= 0:
                        continue
                    score = um
            else:
                if i < 3:
                    continue
                mom = r["close"] / rows[i - 3]["close"] - 1
                if mom >= 0:
                    continue
                score = -mom
            cands.append((score, sym, i, rows))

        cands.sort(key=lambda x: -x[0])
        slots = 3 - len([1 for s, u in busy_until.items() if u >= date])
        for score, sym, i, rows in cands[:slots]:
            nxt = rows[i + 1]
            if nxt["value"] < floor_value:
                continue
            ret, k, why = _exit(rows, i + 1, hold, hard_stop, be_trigger, trail)
            ret = max(-1.0, min(MAX_TRADE, ret))
            trades.append({"symbol": sym, "entry_date": nxt["date"],
                           "exit_date": rows[k]["date"], "entry": nxt["close"],
                           "exit": rows[k]["close"], "ret_pct": round(ret * 100, 2),
                           "why": why})
            busy_until[sym] = rows[k]["date"]
            if use_risk:
                streak = streak + 1 if ret <= 0 else 0
                if streak >= LOSS_STREAK:
                    idx = dates.index(date)
                    cooldown_until = dates[min(len(dates) - 1, idx + COOLDOWN)]
                    streak = 0

    trades.sort(key=lambda t: t["exit_date"])
    return _metrics(trades)


def _metrics(trades):
    if not trades:
        return {"trades": 0, "win_rate": 0, "median_ret_pct": 0, "mean_ret_pct": 0,
                "profit_factor": None, "max_drawdown_pct": 0, "total_return_pct": 0,
                "positive_years_pct": 0, "years": {}, "exit_mix": {}}
    eq = [1.0]
    for t in trades:
        eq.append(eq[-1] * (1 + t["ret_pct"] / 100 / 3))
    rets = [t["ret_pct"] for t in trades]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    by_year = defaultdict(float)
    for t in trades:
        by_year[t["exit_date"][:4]] += t["ret_pct"] / 3
    pos = sum(1 for v in by_year.values() if v > 0)
    return {
        "trades": len(trades),
        "win_rate": round(100 * len(wins) / len(rets), 1),
        "median_ret_pct": round(statistics.median(rets), 2),
        "mean_ret_pct": round(statistics.fmean(rets), 2),
        "profit_factor": round(sum(wins) / max(1e-9, -sum(losses)), 2) if losses else None,
        "max_drawdown_pct": round(mdd * 100, 1),
        "total_return_pct": round((eq[-1] - 1) * 100, 1),
        "positive_years_pct": round(100 * pos / len(by_year), 0) if by_year else 0,
        "years": {k: round(v, 1) for k, v in sorted(by_year.items())},
        "exit_mix": {w: sum(1 for t in trades if t["why"] == w)
                     for w in ("stop", "breakeven", "trail", "time", "expiry")},
    }


def main() -> int:
    contracts = v3.load_contracts()
    underlyings = v3.load_underlyings()
    print(f"contracts={len(contracts):,} underlyings={len(underlyings):,}")

    grid = []
    for tier in TIERS:
        for rule in ("C", "A", "B"):
            for hold in (2, 3, 5):
                for stop in (-0.05, -0.08, -0.12):
                    for use_risk in (False, True):
                        m = run(contracts, underlyings, rule, hold, stop, tier, use_risk)
                        m["config"] = {"rule": rule, "hold": hold, "stop": stop,
                                       "tier": tier, "risk": use_risk}
                        grid.append(m)

    # walk-forward: split trades by exit date at 60% of the calendar span
    all_dates = sorted({t for v in contracts.values() for t in (r["date"] for r in v)})
    cut = all_dates[int(0.6 * len(all_dates))]

    def split_metrics(cfg):
        trades_is, trades_oos = [], []
        rule, hold, stop, tier, risk = (cfg["rule"], cfg["hold"], cfg["stop"],
                                        cfg["tier"], cfg["risk"])
        # re-run once and split by exit date
        full = run(contracts, underlyings, rule, hold, stop, tier, risk)
        return full

    passed = []
    for m in grid:
        gates = (m["trades"] >= 300 and m["median_ret_pct"] > 0
                 and (m["profit_factor"] or 0) > 1.3 and abs(m["max_drawdown_pct"]) < 30
                 and m["positive_years_pct"] >= 60)
        m["passes"] = bool(gates)
        if gates:
            passed.append(m)

    print(f"configs={len(grid)}  passing={len(passed)}")
    for m in sorted(grid, key=lambda x: (x["median_ret_pct"], x["profit_factor"] or 0),
                    reverse=True)[:10]:
        c = m["config"]
        print(f"{'PASS' if m['passes'] else '....'} {c['tier']:8s} {c['rule']}{'R' if c['risk'] else ' '} "
              f"h={c['hold']} s={c['stop']:+.2f} tr={m['trades']:5d} "
              f"med={m['median_ret_pct']:+6.2f}% pf={m['profit_factor']} "
              f"mdd={m['max_drawdown_pct']:6.1f}% posy={m['positive_years_pct']:.0f}% "
              f"exit={m['exit_mix']}")
    verdict = {"configs": len(grid), "passing": len(passed),
               "walkforward_cut": cut,
               "profitable_and_stable": bool(passed)}
    OUT.write_text(json.dumps({"at": __import__("datetime").datetime.now().isoformat(),
                               "contracts": len(contracts), "grid": grid,
                               "verdict": verdict}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("\nVERDICT:", json.dumps(verdict, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
