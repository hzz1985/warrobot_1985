"""Stop-loss variants for F3_QVGM (research only, nothing here touches live code).

Same engine as bt_fund.simulate (copied, baseline path unchanged) plus:
  stop      : None | ("fixed", x) | ("trail", x)
              fixed : close-to-close total-return level of the name <= cost basis * (1 - x)
              trail : level <= running peak of daily closes since entry * (1 - x)
              basis / peak are on the TSETMC total-return index (capital raises / dividends do not fire a stop);
              basis = value-weighted level of the fills (multi-day fills averaged)
  trigger   : after the close of day t -> sell order for t+1 at that session's close price (same fill model as
              rebalance sells): a session locked at the floor all day (high <= floor) = no fill, retried every
              session until sold; fills capped at liq_cap x the session's traded value
  reentry   : "cash"    freed cash sits until the next scheduled rebalance
              "replace" once the stop sale is complete, after that close the best-ranked eligible name that is
                        not held / pending / stopped this cycle is bought from t+1 (equity / n_hold, 3-session
                        retry as for rebalance buys).  "this cycle" resets at each rebalance.
  a stopped name may come back at the next rebalance only through the normal top-10 selection.

python bt_fund_sl.py OUTDIR
"""
from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import bt_fund as B               # noqa: E402
import strategy_fund as SF        # noqa: E402


def simulate_sl(ctx, cfg, start, end, stop=None, reentry="cash", capital=B.CAPITAL):
    m = ctx.m
    i0 = int(np.searchsorted(m.dates, np.datetime64(start)))
    i1 = int(np.searchsorted(m.dates, np.datetime64(end), side="right")) - 1
    cash = capital
    pos, psell, pbuy = {}, {}, {}
    curve, trades = [], []
    traded_amt = 0.0
    expo = []
    need_fund = bool(cfg.pe_max) or any(f in cfg.factors for f in ("ey", "yoy", "roe"))
    next_reb = i0
    stopped_cycle = set()
    need_replace = 0
    st = dict(triggers=0, blocked_sell_days=0, replacements=0, stop_sales_done=0,
              stop_ret=[], stop_lag=[])
    for i in range(i0, i1 + 1):
        r = m.r[i]
        for j, p in pos.items():
            p["v"] *= (1 + r[j])
            if not psell.get(j) and m.traded[i, j]:
                p["peak"] = max(p["peak"], m.tr[i, j])
        # ---- sells
        for j in list(psell):
            p = pos.get(j)
            if p is None:
                psell.pop(j); continue
            if not m.traded[i, j] or m.dn[i, j]:
                if p.get("why") == "stop":
                    st["blocked_sell_days"] += 1
                continue
            amt = min(p["v"], cfg.liq_cap * m.val[i, j])
            if amt <= 0:
                continue
            frac = amt / p["v"] if p["v"] > 0 else 1.0
            cash_in = amt * (1 - cfg.slippage) * (1 - cfg.fee_sell)
            cash += cash_in; traded_amt += amt
            p["proceeds"] += cash_in
            p["v"] -= amt
            if p["v"] <= max(1.0, 0.002 * p["cost"]) or frac >= 0.999:
                cash += max(p["v"], 0); p["proceeds"] += max(p["v"], 0)
                ret = p["proceeds"] / p["cost"] - 1
                trades.append(dict(sym=m.syms[j], entry=ctx.dstr[p["entry_i"]], exit=ctx.dstr[i], ret=ret,
                                   held=i - p["entry_i"], reason=p.get("why", "rebalance")))
                if p.get("why") == "stop":
                    st["stop_sales_done"] += 1; st["stop_ret"].append(ret); st["stop_lag"].append(i - p["trig_i"])
                    if reentry == "replace" and p.get("trig_cycle") == next_reb:
                        need_replace += 1
                pos.pop(j); psell.pop(j)
        # ---- buys
        for j in list(pbuy):
            amt_left, exp = pbuy[j]
            if i > exp or j in psell:
                pbuy.pop(j); continue
            if not m.traded[i, j] or m.up[i, j]:
                continue
            amt = min(amt_left, cash, cfg.liq_cap * m.val[i, j])
            if amt <= 0:
                continue
            cash -= amt; traded_amt += amt
            v = amt / (1 + cfg.fee_buy) / (1 + cfg.slippage)
            p = pos.get(j)
            if p is None:
                p = pos[j] = dict(v=0.0, cost=0.0, entry_i=i, proceeds=0.0, units=0.0, peak=m.tr[i, j])
            p["v"] += v; p["cost"] += amt; p["units"] += v / m.tr[i, j]
            p["basis"] = (p["cost"] / (1 + cfg.fee_buy) / (1 + cfg.slippage)) / p["units"]
            p["peak"] = max(p["peak"], m.tr[i, j])
            left = amt_left - amt
            if left <= 0.02 * amt_left:
                pbuy.pop(j)
            else:
                pbuy[j] = (left, exp)
        eq = cash + sum(p["v"] for p in pos.values())
        curve.append((ctx.dstr[i], eq))
        expo.append(1 - cash / eq if eq > 0 else 0)
        if i == i1:
            break
        # ---- stop checks after the close of session i
        if stop:
            kind, x = stop
            for j, p in pos.items():
                if j in psell or not m.traded[i, j]:
                    continue
                lvl = m.tr[i, j]
                ref = p["basis"] if kind == "fixed" else p["peak"]
                if lvl <= ref * (1 - x):
                    psell[j] = p["v"]; p["why"] = "stop"; p["trig_i"] = i; p["trig_cycle"] = next_reb
                    stopped_cycle.add(j); st["triggers"] += 1
                    pbuy.pop(j, None)
        is_reb = i >= next_reb
        if not is_reb:
            if need_replace > 0:
                rows = B.rows_at(ctx, i, cfg, need_fund=need_fund)
                ranked = SF.rank(rows, cfg)
                for row in ranked:
                    if need_replace <= 0:
                        break
                    j = m.si[row["sym"]]
                    if j in pos or j in pbuy or j in stopped_cycle:
                        continue
                    pbuy[j] = (eq / cfg.n_hold, i + 3)
                    need_replace -= 1; st["replacements"] += 1
                need_replace = 0
            continue
        # ---- scheduled rebalance
        next_reb = i + cfg.rebalance_sessions
        stopped_cycle = set(); need_replace = 0
        held = [m.syms[j] for j in pos if j not in psell]
        rows = B.rows_at(ctx, i, cfg, need_fund=need_fund)
        target = SF.select(SF.rank(rows, cfg), held, cfg)
        tj = {m.si[s] for s in target}
        for j, p in pos.items():
            if j not in tj and j not in psell:
                psell[j] = p["v"]; p["why"] = "rebalance"
        pbuy = {j: v for j, v in pbuy.items() if j in tj}
        for s in target:
            j = m.si[s]
            if j in pos or j in pbuy:
                continue
            pbuy[j] = (eq / cfg.n_hold, i + 3)
    for j, p in pos.items():
        trades.append(dict(sym=m.syms[j], entry=ctx.dstr[p["entry_i"]], exit="open",
                           ret=(p["proceeds"] + p["v"] * (1 - cfg.fee_sell)) / p["cost"] - 1,
                           held=i1 - p["entry_i"], reason="open"))
    eqs = np.array([v for _, v in curve])
    years = max(1e-9, len(curve) / 240.0)
    return dict(curve=curve, trades=trades, turnover=traded_amt / 2 / max(1.0, eqs.mean()) / years,
                exposure=float(np.mean(expo)) if expo else 0.0, stats=st)


def tail(res):
    eq = pd.Series([v for _, v in res["curve"]], index=pd.to_datetime([d for d, _ in res["curve"]]))
    dr = eq.pct_change().dropna()
    q = dr.quantile(0.05)
    t = pd.DataFrame(res["trades"])
    mo = eq.resample("ME").last().pct_change().dropna()
    dd = 1 - eq / eq.cummax()
    return dict(cvar5_daily=round(float(dr[dr <= q].mean() * 100), 2),
                worst_day=round(float(dr.min() * 100), 2),
                worst_month=round(float(mo.min() * 100), 1) if len(mo) else None,
                worst_trade=round(float(t.ret.min() * 100), 1) if len(t) else None,
                ulcer=round(float(np.sqrt((dd ** 2).mean()) * 100), 2))


VARS = [("baseline", None, "cash")]
for x in (0.08, 0.10, 0.12, 0.15, 0.20):
    for re in ("cash", "replace"):
        VARS.append((f"fixed{int(x*100)}_{re}", ("fixed", x), re))
for x in (0.15, 0.20):
    for re in ("cash", "replace"):
        VARS.append((f"trail{int(x*100)}_{re}", ("trail", x), re))


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    (out / "curves").mkdir(exist_ok=True); (out / "trades").mkdir(exist_ok=True)
    only = set(sys.argv[2:])
    t0 = time.time()
    import os, pickle
    cp = os.environ.get("CTX_PKL", "/workspace/work/f3sl_ctx.pkl")
    ctx = pickle.load(open(cp, "rb")) if os.path.exists(cp) else B.get_ctx()
    cfg = SF.VARIANTS["F3_QVGM"]
    print("ctx", round(time.time() - t0), flush=True)
    rows = []
    for name, stop, re in VARS:
        if only and name not in only:
            continue
        for per, (a, b) in B.PERIODS.items():
            r = simulate_sl(ctx, cfg, a, b, stop=stop, reentry=re)
            mt = B.metrics(r); mt.update(tail(r))
            s = r["stats"]
            rows.append(dict(variant=name, period=per, stop=str(stop), reentry=re, **mt,
                             triggers=s["triggers"], stop_sales=s["stop_sales_done"],
                             blocked_sell_days=s["blocked_sell_days"], replacements=s["replacements"],
                             avg_stop_exit_ret=round(float(np.mean(s["stop_ret"]) * 100), 2) if s["stop_ret"] else None,
                             avg_stop_lag=round(float(np.mean(s["stop_lag"])), 2) if s["stop_lag"] else None,
                             max_stop_lag=int(max(s["stop_lag"])) if s["stop_lag"] else None))
            pd.DataFrame(r["curve"], columns=["date", "equity"]).to_csv(out / "curves" / f"{name}_{per}.csv", index=False)
            pd.DataFrame(r["trades"]).to_csv(out / "trades" / f"{name}_{per}.csv", index=False, encoding="utf-8-sig")
            print(name, per, mt["total_return"], mt["max_dd"], s["triggers"], round(time.time() - t0), flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / (os.environ.get("RES_NAME") or "results_sl.csv"), index=False)
    print(df.to_string())
