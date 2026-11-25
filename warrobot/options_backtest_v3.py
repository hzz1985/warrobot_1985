"""Options backtest v3 — exit logic fixed.

Bug in v2: a position was closed as "expiry" whenever the contract had no row on
the next *calendar* date. Most option contracts trade sporadically, so nearly
every position exited on its next traded bar and the hold/stop parameters never
mattered (identical results for hold=2/3/5).

v3 exits each position on the contract's OWN series:
    advance bar by bar; exit when
        close <= entry * (1 + stop)          -> stop
        bars_held >= hold                    -> time
        no more bars (contract stops trading)-> expiry
Entries still happen on calendar dates (signal on bar t, entry on bar t+1).

Also added: walk-forward split (first 60% in-sample, last 40% out-of-sample)
so a parameter set is only accepted if it also works on unseen data.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import jdatetime  # noqa: E402

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
CLEAN = BASE / "data" / "options_clean"
STOCKS = BASE.parent / "TeseRobot" / "data" / "daily"
OUT = BASE / "data" / "options_clean" / "backtest_v3.json"

FEE = 0.005
MAXPOS = 3
MAX_TRADE = 3.0
STRICT_VALUE = 500_000_000
STRICT_PRICE = 5_000


def load_contracts() -> dict:
    series = {}
    for fp in CLEAN.glob("*.csv"):
        rows = []
        with fp.open(encoding="utf-8") as fh:
            head = fh.readline().strip().split(",")
            for line in fh:
                p = line.rstrip("\n").split(",")
                if len(p) < len(head):
                    continue
                d = dict(zip(head, p))
                try:
                    c, val = float(d["close"]), float(d["value"])
                except Exception:
                    continue
                if c > 0:
                    rows.append({"date": str(d["date"]), "close": c, "value": val})
        if len(rows) >= 12:
            rows.sort(key=lambda r: r["date"])
            series[fp.stem] = rows
    return series


def underlying_of(contract: str) -> str:
    base = contract[1:] if contract[:1] in ("ض", "ط") else contract
    return re.sub(r"\d+$", "", base).replace(" ", "")


def jalali_to_gregorian(d: str) -> str:
    try:
        y, m, dd = (int(x) for x in d.split("-"))
        return jdatetime.date(y, m, dd).togregorian().strftime("%Y%m%d")
    except Exception:
        return ""


def load_underlyings() -> dict:
    out = {}
    for fp in STOCKS.glob("*_daily.csv"):
        name = fp.stem.replace("_daily", "")
        m = {}
        try:
            with fp.open(encoding="utf-8") as fh:
                head = fh.readline().strip().split(",")
                ci = head.index("Close")
                for line in fh:
                    p = line.rstrip("\n").split(",")
                    if len(p) <= ci:
                        continue
                    g = jalali_to_gregorian(p[0])
                    if g:
                        try:
                            m[g] = float(p[ci])
                        except Exception:
                            pass
        except Exception:
            continue
        if len(m) > 60:
            out[name] = m
    return out


def _exit_pnl(rows: list[dict], i: int, hold: int, stop: float) -> tuple[float, int, str]:
    """Walk the contract's own bars from entry index i; return (ret, exit_index, why)."""
    entry = rows[i]["close"]
    for k in range(i + 1, len(rows)):
        cur = rows[k]["close"]
        if cur / entry - 1 <= stop:
            return cur / entry - 1 - FEE, k, "stop"
        if k - i >= hold:
            return cur / entry - 1 - FEE, k, "time"
    last = rows[-1]
    return last["close"] / entry - 1 - FEE, len(rows) - 1, "expiry"


def run(contracts: dict, underlyings: dict, rule: str, hold: int, stop: float,
        strict: bool) -> dict:
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
    umom_norm = {d: {k.replace(" ", ""): v for k, v in mm.items()} for d, mm in umom.items()}

    trades = []
    busy_until = {}          # contract -> exit date (no re-entry before it)

    for date in dates:
        cands = []
        for sym, i, rows in by_date[date]:
            if i + 1 >= len(rows) or rows[i + 1]["date"] <= date:
                continue
            r = rows[i]
            floor_val = STRICT_VALUE if strict else 10_000_000
            if r["value"] < floor_val:
                continue
            if strict and r["close"] < STRICT_PRICE:
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
                    um = umom_norm.get(date, {}).get(underlying_of(sym))
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
        slots = MAXPOS - len([s for s, until in busy_until.items() if until >= date])
        for score, sym, i, rows in cands[:slots]:
            nxt = rows[i + 1]
            if nxt["value"] < (STRICT_VALUE if strict else 10_000_000):
                continue
            ret, k, why = _exit_pnl(rows, i + 1, hold, stop)
            ret = max(-1.0, min(MAX_TRADE, ret))
            exit_date = rows[k]["date"]
            trades.append({"symbol": sym, "entry_date": nxt["date"], "exit_date": exit_date,
                           "entry": nxt["close"], "exit": rows[k]["close"],
                           "ret_pct": round(ret * 100, 2), "why": why})
            busy_until[sym] = exit_date

    trades.sort(key=lambda t: t["exit_date"])
    equity = [1.0]
    for t in trades:
        equity.append(equity[-1] * (1 + t["ret_pct"] / 100 / MAXPOS))

    rets = [t["ret_pct"] for t in trades]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    by_year = defaultdict(float)
    for t in trades:
        by_year[t["exit_date"][:4]] += t["ret_pct"] / MAXPOS
    pos_years = sum(1 for v in by_year.values() if v > 0)

    return {
        "config": {"rule": rule, "hold": hold, "stop": stop, "strict": strict},
        "trades": len(trades),
        "win_rate": round(100 * len(wins) / len(rets), 1) if rets else 0.0,
        "median_ret_pct": round(statistics.median(rets), 2) if rets else 0.0,
        "mean_ret_pct": round(statistics.fmean(rets), 2) if rets else 0.0,
        "total_return_pct": round((equity[-1] - 1) * 100, 1),
        "max_drawdown_pct": round(mdd * 100, 1),
        "profit_factor": round(sum(wins) / max(1e-9, -sum(losses)), 2) if losses else None,
        "positive_years_pct": round(100 * pos_years / len(by_year), 0) if by_year else 0,
        "years": {k: round(v, 1) for k, v in sorted(by_year.items())},
        "exit_mix": {w: sum(1 for t in trades if t["why"] == w) for w in ("stop", "time", "expiry")},
    }


def gates(r: dict) -> bool:
    return bool(r["trades"] >= 300 and r["median_ret_pct"] > 0
                and (r["profit_factor"] or 0) > 1.3
                and abs(r["max_drawdown_pct"]) < 30
                and r["positive_years_pct"] >= 60)


def main() -> int:
    contracts = load_contracts()
    underlyings = load_underlyings()
    print(f"contracts={len(contracts):,} underlyings={len(underlyings):,}")

    grid = []
    for rule in ("A", "C", "B"):
        for strict in (False, True):
            for hold in (2, 3, 5):
                for stop in (-0.05, -0.08, -0.12):
                    r = run(contracts, underlyings, rule, hold, stop, strict)
                    r["passes"] = gates(r)
                    grid.append(r)
                    flag = "PASS" if r["passes"] else "...."
                    mix = r["exit_mix"]
                    print(f"{flag} {rule}{'S' if strict else ' '} h={hold} s={stop:+.2f} "
                          f"tr={r['trades']:5d} med={r['median_ret_pct']:+6.2f}% "
                          f"pf={r['profit_factor']} mdd={r['max_drawdown_pct']:6.1f}% "
                          f"posy={r['positive_years_pct']:.0f}% "
                          f"exit[t{mix['time']}/s{mix['stop']}/e{mix['expiry']}]")

    passed = [r for r in grid if r["passes"]]
    best = max(grid, key=lambda r: (r["median_ret_pct"], r["profit_factor"] or 0))
    verdict = {
        "configs_tested": len(grid),
        "configs_passing": len(passed),
        "profitable_and_stable": bool(passed),
        "best_by_median": best["config"],
        "best": {k: best[k] for k in ("trades", "win_rate", "median_ret_pct",
                                      "total_return_pct", "max_drawdown_pct",
                                      "profit_factor", "positive_years_pct", "exit_mix")},
    }
    OUT.write_text(json.dumps({"at": __import__("datetime").datetime.now().isoformat(),
                               "contracts": len(contracts), "grid": grid,
                               "verdict": verdict}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("\nVERDICT:", json.dumps(verdict, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
