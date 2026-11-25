"""Options backtest v2 — fixes rule C and sweeps the liquidity/parameter space.

What changed vs v1:
  * rule C is now REAL underlying momentum: contract name -> underlying symbol,
    underlying 5-day momentum from TeseRobot's stock daily files (Jalali dates
    converted with jdatetime), buy calls whose underlying is in an uptrend
  * rule A gets a strict variant: turnover >= 500M rial and price >= 5,000 rial
  * parameter sweep over hold in {2,3,5} and stop in {-5%,-8%,-12%}
  * the verdict is decided ONLY by the robust gates defined up-front:
        trades >= 300 · median trade > 0 · PF > 1.3 · |MDD| < 30%
        · positive years >= 60%
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
OUT = BASE / "data" / "options_clean" / "backtest_v2.json"

FEE = 0.005
MAXPOS = 3
MAX_TRADE = 3.0
MIN_VALUE = 10_000_000
STRICT_VALUE = 500_000_000
STRICT_PRICE = 5_000


# ---------------------------------------------------------------- data loading
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
                    c, v, val = float(d["close"]), float(d["volume"]), float(d["value"])
                except Exception:
                    continue
                if c > 0:
                    rows.append({"date": str(d["date"]), "close": c, "volume": v, "value": val})
        if len(rows) >= 12:
            rows.sort(key=lambda r: r["date"])
            series[fp.stem] = rows
    return series


def underlying_of(contract: str) -> str:
    """ضهرم5019 -> هرم , طصاد5016 -> صاد (the leading ض/ط marks call/put)."""
    base = contract[1:] if contract[:1] in ("ض", "ط") else contract
    return re.sub(r"\d+$", "", base)


def jalali_to_gregorian(d: str) -> str:
    try:
        y, m, dd = (int(x) for x in d.split("-"))
        return jdatetime.date(y, m, dd).togregorian().strftime("%Y%m%d")
    except Exception:
        return ""


def load_underlyings() -> dict:
    """underlying -> {gregorian_date: close} for the stocks we have."""
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


# ---------------------------------------------------------------- the engine
def run(contracts: dict, underlyings: dict, rule: str, hold: int, stop: float,
        strict: bool) -> dict:
    by_date = defaultdict(list)
    for sym, rows in contracts.items():
        for i, r in enumerate(rows):
            by_date[r["date"]].append((sym, i, rows))
    dates = sorted(by_date)

    # precompute underlying momentum series {date: {underlying: mom5}}
    umom = {}
    if rule == "C":
        all_dates = sorted({d for m in underlyings.values() for d in m})
        for u, m in underlyings.items():
            ds = sorted(m)
            for i in range(5, len(ds)):
                umom.setdefault(ds[i], {})[u] = m[ds[i]] / m[ds[i - 5]] - 1

    equity = [1.0]
    trades = []
    open_pos = []

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
            rows = contracts[p["symbol"]]
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
            r = rows[i]
            floor = STRICT_VALUE if strict else MIN_VALUE
            if r["value"] < floor:
                continue
            if strict and r["close"] < STRICT_PRICE:
                continue
            if rule in ("A", "C"):
                n = 5
                if i < n:
                    continue
                mom = r["close"] / rows[i - n]["close"] - 1
                if rule == "A":
                    if mom <= 0:
                        continue
                    score = mom
                else:
                    u = underlying_of(sym).replace(" ", "")
                    um = None
                    for cand_k, cand_v in umom.get(date, {}).items():
                        if cand_k.replace(" ", "") == u:
                            um = cand_v
                            break
                    if um is None or um <= 0:
                        continue
                    score = um
            else:
                n = 3
                if i < n:
                    continue
                mom = r["close"] / rows[i - n]["close"] - 1
                if mom >= 0:
                    continue
                score = -mom
            cands.append((score, sym, i, rows))
        cands.sort(key=lambda x: -x[0])
        for score, sym, i, rows in cands[: MAXPOS - len(open_pos)]:
            nxt = rows[i + 1]
            floor = STRICT_VALUE if strict else MIN_VALUE
            if nxt["value"] < floor:
                continue
            open_pos.append({"symbol": sym, "entry": nxt["close"], "entry_date": nxt["date"],
                             "i": i + 1, "entry_i": i + 1})

    for p in open_pos:
        rows = contracts[p["symbol"]]
        close_pos(p, rows[-1]["close"], rows[-1]["date"], "end")

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

    res = {
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
    }
    res["passes"] = bool(
        res["trades"] >= 300 and res["median_ret_pct"] > 0
        and (res["profit_factor"] or 0) > 1.3 and abs(res["max_drawdown_pct"]) < 30
        and res["positive_years_pct"] >= 60)
    return res


def main() -> int:
    contracts = load_contracts()
    underlyings = load_underlyings()
    print(f"contracts={len(contracts):,} underlyings={len(underlyings):,}")
    if len(contracts) < 50:
        print("not enough contracts")
        return 1

    grid = []
    for rule in ("A", "C", "B"):
        for strict in (False, True):
            for hold in (2, 3, 5):
                for stop in (-0.05, -0.08, -0.12):
                    r = run(contracts, underlyings, rule, hold, stop, strict)
                    grid.append(r)
                    flag = "PASS" if r["passes"] else "...."
                    print(f"{flag} {rule}{'S' if strict else ' '} hold={hold} stop={stop:+.2f} "
                          f"tr={r['trades']:5d} med={r['median_ret_pct']:+6.2f}% "
                          f"pf={r['profit_factor']} mdd={r['max_drawdown_pct']:6.1f}% "
                          f"posy={r['positive_years_pct']:.0f}%")

    passed = [r for r in grid if r["passes"]]
    best = max(grid, key=lambda r: (r["median_ret_pct"], r["profit_factor"] or 0))
    verdict = {
        "configs_tested": len(grid),
        "configs_passing": len(passed),
        "profitable_and_stable": bool(passed),
        "best_by_median": best["config"],
        "best_median_ret_pct": best["median_ret_pct"],
        "honest_note": ("no configuration passed the up-front gates"
                        if not passed else "at least one configuration passed"),
    }
    OUT.write_text(json.dumps({"at": __import__("datetime").datetime.now().isoformat(),
                               "contracts": len(contracts), "underlyings": len(underlyings),
                               "grid": grid, "verdict": verdict},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nVERDICT:", json.dumps(verdict, ensure_ascii=False))
    print("saved:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
