"""Options v5 — the full next-step table, executed.

1) real walk-forward        : 60% in-sample / 40% out-of-sample by exit date;
                              the candidate must be positive on BOTH
2) cooldown review          : the same config with and without the 3-loss
                              cooldown, compared side by side
3) sensitivity              : hold and stop moved +/-20%, plus the neighbouring
                              liquidity tier; the median must stay positive in
                              the majority of variants
4) final decision           : explicit accept / reject with the reasons printed
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
OUT = BASE / "data" / "options_clean" / "backtest_v5.json"

FEE = 0.005
MAX_TRADE = 3.0
TIERS = {"A_500m": (500_000_000, 5_000), "B_100m": (100_000_000, 2_000)}
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
        elif ret >= be_trigger:
            be_done = True
        if k - i >= hold:
            return ret - FEE, k, "time"
    last = rows[-1]
    return last["close"] / entry - 1 - FEE, len(rows) - 1, "expiry"


def run_trades(contracts, underlyings, rule, hold, stop, tier,
               use_risk=True, cooldown=COOLDOWN, be=BE_TRIGGER, trail=TRAIL):
    floor_value, floor_price = TIERS[tier]
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
    busy = {}
    streak = 0
    cooldown_until = ""

    for date in dates:
        if use_risk and cooldown and date <= cooldown_until:
            continue
        cands = []
        for sym, i, rows in by_date[date]:
            if i + 1 >= len(rows) or rows[i + 1]["date"] <= date:
                continue
            r = rows[i]
            if r["value"] < floor_value or r["close"] < floor_price:
                continue
            if sym in busy and busy[sym] >= date:
                continue
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
            cands.append((score, sym, i, rows))

        cands.sort(key=lambda x: -x[0])
        slots = 3 - len([1 for s, u in busy.items() if u >= date])
        for score, sym, i, rows in cands[:slots]:
            nxt = rows[i + 1]
            if nxt["value"] < floor_value:
                continue
            ret, k, why = _exit(rows, i + 1, hold, stop, be, trail)
            ret = max(-1.0, min(MAX_TRADE, ret))
            trades.append({"symbol": sym, "entry_date": nxt["date"],
                           "exit_date": rows[k]["date"], "ret_pct": round(ret * 100, 2),
                           "why": why})
            busy[sym] = rows[k]["date"]
            if use_risk:
                streak = streak + 1 if ret <= 0 else 0
                if streak >= LOSS_STREAK:
                    idx = dates.index(date)
                    cooldown_until = dates[min(len(dates) - 1, idx + cooldown)]
                    streak = 0
    trades.sort(key=lambda t: t["exit_date"])
    return trades


def metrics(trades):
    if not trades:
        return {"trades": 0, "win_rate": 0, "median_ret_pct": 0, "profit_factor": None,
                "max_drawdown_pct": 0, "total_return_pct": 0, "positive_years_pct": 0}
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
        "profit_factor": round(sum(wins) / max(1e-9, -sum(losses)), 2) if losses else None,
        "max_drawdown_pct": round(mdd * 100, 1),
        "total_return_pct": round((eq[-1] - 1) * 100, 1),
        "positive_years_pct": round(100 * pos / len(by_year), 0) if by_year else 0,
    }


def walk_forward(trades):
    """Split by exit date: first 60% of dates in-sample, the rest out-of-sample."""
    dates = sorted({t["exit_date"] for t in trades})
    if len(dates) < 10:
        return None, None, None
    cut = dates[int(0.6 * len(dates))]
    ins = [t for t in trades if t["exit_date"] <= cut]
    oos = [t for t in trades if t["exit_date"] > cut]
    return metrics(ins), metrics(oos), cut


def main() -> int:
    contracts = v3.load_contracts()
    underlyings = v3.load_underlyings()
    print(f"contracts={len(contracts):,} underlyings={len(underlyings):,}\n")

    base = {"rule": "C", "hold": 5, "stop": -0.12, "tier": "A_500m"}

    # ---------- 1) walk-forward on the candidate
    tr = run_trades(contracts, underlyings, base["rule"], base["hold"], base["stop"],
                    base["tier"], use_risk=True)
    ins, oos, cut = walk_forward(tr)
    total = metrics(tr)
    print("=== 1) walk-forward ===")
    print(f"candidate: {base}")
    print(f"  total   : {json.dumps(total, ensure_ascii=False)}")
    print(f"  cut date: {cut}")
    print(f"  in-sample : {json.dumps(ins, ensure_ascii=False)}")
    print(f"  out-of-sample: {json.dumps(oos, ensure_ascii=False)}")

    # ---------- 2) cooldown review
    tr_nc = run_trades(contracts, underlyings, base["rule"], base["hold"], base["stop"],
                       base["tier"], use_risk=False)
    m_nc = metrics(tr_nc)
    tr_cd = run_trades(contracts, underlyings, base["rule"], base["hold"], base["stop"],
                       base["tier"], use_risk=True, cooldown=COOLDOWN)
    m_cd = metrics(tr_cd)
    print("\n=== 2) cooldown review ===")
    print(f"  without risk rules: {json.dumps(m_nc, ensure_ascii=False)}")
    print(f"  with cooldown     : {json.dumps(m_cd, ensure_ascii=False)}")

    # ---------- 3) sensitivity +/-20%
    print("\n=== 3) sensitivity (hold and stop +/-20%) ===")
    variants = []
    for hold in {round(base["hold"] * 0.8), base["hold"], round(base["hold"] * 1.2)}:
        for stop in (round(base["stop"] * 0.8, 3), base["stop"], round(base["stop"] * 1.2, 3)):
            for tier in ("A_500m", "B_100m"):
                t = run_trades(contracts, underlyings, base["rule"], max(1, hold), stop, tier)
                m = metrics(t)
                m.update({"hold": hold, "stop": stop, "tier": tier})
                variants.append(m)
                print(f"  hold={hold} stop={stop:+.3f} tier={tier:8s} "
                      f"tr={m['trades']:4d} med={m['median_ret_pct']:+6.2f}% "
                      f"pf={m['profit_factor']} mdd={m['max_drawdown_pct']:6.1f}%")
    positive = sum(1 for v in variants if v["median_ret_pct"] > 0)
    print(f"  positive-median variants: {positive}/{len(variants)}")

    # ---------- 4) final decision
    checks = {
        "oos_positive": bool(oos and oos["median_ret_pct"] > 0),
        "oos_pf_above_1.2": bool(oos and (oos["profit_factor"] or 0) > 1.2),
        "ins_positive": bool(ins and ins["median_ret_pct"] > 0),
        "mdd_under_30": abs(total["max_drawdown_pct"]) < 30,
        "sensitivity_stable": positive >= int(0.7 * len(variants)),
        "trades_at_least_80": total["trades"] >= 80,
    }
    accepted = all(checks.values())
    decision = {
        "accepted": accepted,
        "checks": checks,
        "candidate": base,
        "total": total,
        "in_sample": ins,
        "out_of_sample": oos,
        "cooldown_with": m_cd,
        "cooldown_without": m_nc,
        "sensitivity_positive": f"{positive}/{len(variants)}",
        "reason": ("all gates passed" if accepted else
                   "failed: " + ", ".join(k for k, v in checks.items() if not v)),
    }
    OUT.write_text(json.dumps({"at": __import__("datetime").datetime.now().isoformat(),
                               "variants": variants, "decision": decision},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== 4) FINAL DECISION ===")
    print(json.dumps(decision, ensure_ascii=False, indent=1)[:1600])
    print("saved:", OUT)
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
