"""Backtest of the fundamentals strategy (strategy_fund.py) — portfolio level, point in time.

Mechanics (see PREREGISTERED_VARIANTS.md):
  * signal after the close of day t (fundamentals published <= t, prices <= t)
  * orders execute from session t+1 at that session's final price (pClosing) +- slippage; a session locked at
    the ceiling all day cannot be bought, one locked at the floor cannot be sold; fills capped at liq_cap x the
    session's traded value; unfilled buys retried 3 sessions, unfilled sells until done
  * fees buy 0.3712 %, sell 0.88 %; equal weight equity / n_hold per new name; sells before buys
  * returns per session vs the TSETMC reference price (capital raises / dividends handled as total return)

python bt_fund.py OUTDIR            all variants, IS / OOS / full, benchmarks, placebo
"""
from __future__ import annotations

import json
import math
import os
import pickle
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import data_fund as DF            # noqa: E402
import strategy_fund as SF        # noqa: E402

PERIODS = {"IS": ("2024-08-01", "2025-09-30"), "OOS": ("2025-10-01", "2026-11-25")}
LONG = {"2018-2020": ("2018-03-21", "2020-12-31"), "2021-2022": ("2021-01-01", "2022-12-31"),
        "2023-2024": ("2023-01-01", "2024-07-31")}
CAPITAL = float(os.environ.get("BT_CAPITAL", 20e9))


class Ctx:
    def __init__(self):
        p = DF.load_panel()
        self.m = DF.Market(p)
        self.flow = DF.load_flow(self.m)
        self.F = DF.Fundamentals(self.m.syms)
        self.dstr = pd.to_datetime(self.m.dates).strftime("%Y-%m-%d").to_numpy()
        self._snap = {}

    def snap(self, s, i):
        k = (s, i)
        if k not in self._snap:
            self._snap[k] = self.F.snapshot(s, self.dstr[i])
        return self._snap[k]


def get_ctx(rebuild=False) -> Ctx:
    return Ctx()


def liquid_mask(ctx, i, cfg):
    m = ctx.m
    if cfg.min_value_rial < 0:          # relative floor: top |x| share of traded stocks by 60-session value
        v = np.where(m.traded[i] & (np.nan_to_num(m.traded60[i]) >= cfg.min_traded_frac), np.nan_to_num(m.val60[i]), 0.0)
        pos = v[v > 0]
        if not len(pos):
            return v > 0
        cut = np.quantile(pos, 1 + cfg.min_value_rial)
        return v >= cut
    return (np.nan_to_num(m.traded60[i]) >= cfg.min_traded_frac) & (np.nan_to_num(m.val60[i]) >= cfg.min_value_rial) \
        & m.traded[i]


def rows_at(ctx, i, cfg, need_fund=True):
    m = ctx.m
    liq = liquid_mask(ctx, i, cfg)
    mom = m.mom(i, cfg.mom_lb, cfg.mom_skip)
    out = []
    for j in np.where(liq)[0]:
        s = m.syms[j]
        row = dict(sym=s, j=j, liquid=True, mom=float(mom[j]), flow=float(ctx.flow[i, j]),
                   np_ttm=math.nan, mcap=math.nan, yoy=math.nan, roe=math.nan)
        sn = ctx.snap(s, i) if need_fund else None
        if sn is None or not sn.get("pub"):
            if need_fund:
                continue
            out.append(row)
            continue
        row.update(np_ttm=sn["np_ttm"], yoy=sn["yoy"], roe=sn["roe"], src=sn["source"], pub=sn["pub"])
        cap = sn["capital"]
        if cap and not math.isnan(cap):
            k = int(np.searchsorted(m.dates, np.datetime64(sn["pub"]), side="right")) - 1
            while k >= 0 and not m.traded[k, j]:
                k -= 1
            if k >= 0:
                row["mcap"] = cap / 1000.0 * m.c[k, j] * m.tr[i, j] / m.tr[k, j]
        out.append(row)
    return out


def simulate(ctx, cfg: SF.FundConfig, start, end, ranker=None, rng=None, capital=CAPITAL, record=True):
    m = ctx.m
    i0 = int(np.searchsorted(m.dates, np.datetime64(start)))
    i1 = int(np.searchsorted(m.dates, np.datetime64(end), side="right")) - 1
    need_fund = bool(cfg.pe_max) or any(f in cfg.factors for f in ("ey", "yoy", "roe")) or cfg.mode == "pead"
    reg = m.regime_ok(cfg.regime_ma) if cfg.regime_ma else None
    cash = capital
    pos = {}            # j -> dict(v, cost, entry_i, proceeds, sold_frac)
    psell, pbuy = {}, {}   # j -> remaining value to sell ; j -> (amount, expiry_i)
    curve, trades = [], []
    traded_amt = 0.0
    expo = []
    keeps = []
    next_reb = i0
    for i in range(i0, i1 + 1):
        r = m.r[i]
        for j, p in pos.items():
            p["v"] *= (1 + r[j])
        # ---- executions at session i
        for j in list(psell):
            p = pos.get(j)
            if p is None:
                psell.pop(j); continue
            if not m.traded[i, j] or m.dn[i, j]:
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
                if record:
                    trades.append(dict(sym=m.syms[j], entry=ctx.dstr[p["entry_i"]], exit=ctx.dstr[i],
                                       ret=p["proceeds"] / p["cost"] - 1, held=i - p["entry_i"], reason=p.get("why", "rebalance")))
                pos.pop(j); psell.pop(j)
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
            p = pos.setdefault(j, dict(v=0.0, cost=0.0, entry_i=i, proceeds=0.0))
            p["v"] += v; p["cost"] += amt
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
        # ---- signals after the close of session i
        if cfg.mode == "pead":
            for j, p in pos.items():
                if i - p["entry_i"] >= cfg.pead_hold and j not in psell:
                    psell[j] = p["v"]; p["why"] = "time"
            slots = cfg.n_hold - len([j for j in pos if j not in psell]) - len(pbuy)
            if slots > 0:
                prev = ctx.dstr[i - 1] if i > 0 else ctx.dstr[i]
                cands = []
                for row in rows_at(ctx, i, cfg, need_fund=True):
                    j = row["j"]
                    if j in pos or j in pbuy or row["sym"] in SF.MANUAL_ONLY:
                        continue
                    pub = row.get("pub")
                    if not pub or not (prev < pub <= ctx.dstr[i]):
                        continue
                    y = row.get("yoy")
                    if y is None or math.isnan(y) or y < cfg.pead_yoy:
                        continue
                    if not (row["np_ttm"] > 0 and row["mcap"] > 0):
                        continue
                    cands.append((-(y if ranker is None else rng.random()), row["sym"], j))
                cands.sort()
                for _, s, j in cands[:slots]:
                    pbuy[j] = (eq / cfg.n_hold, i + 3)
            continue
        if i < next_reb:
            continue
        next_reb = i + cfg.rebalance_sessions
        held = [m.syms[j] for j in pos if j not in psell]
        if reg is not None and not reg[i]:
            target = []
        else:
            rows = rows_at(ctx, i, cfg, need_fund=need_fund)
            ranked = SF.rank(rows, cfg) if ranker is None else ranker(rows, cfg, rng, held)
            target = SF.select(ranked, held, cfg)
            if held:
                keeps.append(len(set(held) & set(target)) / len(held))
        tj = {m.si[s] for s in target}
        for j, p in pos.items():
            if j not in tj and j not in psell:
                psell[j] = p["v"]; p["why"] = "rebalance" if target or reg is None else "regime"
        pbuy = {j: v for j, v in pbuy.items() if j in tj}
        for s in target:
            j = m.si[s]
            if j in pos or j in pbuy:
                continue
            pbuy[j] = (eq / cfg.n_hold, i + 3)
    # mark open positions
    if record:
        for j, p in pos.items():
            trades.append(dict(sym=m.syms[j], entry=ctx.dstr[p["entry_i"]], exit="open",
                               ret=(p["proceeds"] + p["v"] * (1 - cfg.fee_sell)) / p["cost"] - 1,
                               held=i1 - p["entry_i"], reason="open"))
    eqs = np.array([v for _, v in curve])
    years = max(1e-9, len(curve) / 240.0)
    return dict(curve=curve, trades=trades, turnover=traded_amt / 2 / max(1.0, eqs.mean()) / years,
                exposure=float(np.mean(expo)) if expo else 0.0,
                retention=float(np.mean(keeps)) if keeps else None)


def metrics(res) -> dict:
    eq = pd.Series([v for _, v in res["curve"]], index=pd.to_datetime([d for d, _ in res["curve"]]))
    if len(eq) < 3:
        return {}
    days = (eq.index[-1] - eq.index[0]).days or 1
    dr = eq.pct_change().dropna()
    out = dict(start=str(eq.index[0].date()), end=str(eq.index[-1].date()),
               total_return=round((eq.iloc[-1] / eq.iloc[0] - 1) * 100, 1),
               cagr=round(((eq.iloc[-1] / eq.iloc[0]) ** (365.25 / days) - 1) * 100, 1),
               max_dd=round(float((1 - eq / eq.cummax()).max()) * 100, 1),
               sharpe=round(float(dr.mean() / dr.std() * math.sqrt(240)), 2) if dr.std() > 0 else 0.0)
    t = pd.DataFrame(res.get("trades") or [])
    out["trades"] = int(len(t))
    if len(t):
        w, l = t[t.ret > 0], t[t.ret <= 0]
        out.update(win_rate=round(float((t.ret > 0).mean() * 100), 1),
                   avg_win=round(float(w.ret.mean() * 100), 2) if len(w) else None,
                   avg_loss=round(float(l.ret.mean() * 100), 2) if len(l) else None,
                   avg_hold=round(float(t.held.mean()), 1), open_at_end=int((t.exit == "open").sum()))
    if "turnover" in res:
        out["turnover_x_per_year"] = round(res["turnover"], 2)
        out["exposure_pct"] = round(res["exposure"] * 100, 1)
    return out


def ew_bench(ctx, start, end):
    m = ctx.m
    i0 = int(np.searchsorted(m.dates, np.datetime64(start)))
    i1 = int(np.searchsorted(m.dates, np.datetime64(end), side="right")) - 1
    eq = np.cumprod(1 + m.ew_ret[i0:i1 + 1])
    return metrics(dict(curve=list(zip(ctx.dstr[i0:i1 + 1], eq))))


def tedpix(start, end):
    idx = pd.read_csv(f"{DF.TSE}/tedpix_tgju.csv", parse_dates=["date"])
    s = idx[(idx.date >= start) & (idx.date <= end)]
    return metrics(dict(curve=list(zip(s.date.dt.strftime("%Y-%m-%d"), s.close))))


def _shuffle_keep(el, rng, held, keep_p):
    rng.shuffle(el)
    if held and keep_p is not None:
        hs = set(held)
        kept = [r for r in el if r["sym"] in hs and rng.random() < keep_p]
        ks = {r["sym"] for r in kept}
        el = kept + [r for r in el if r["sym"] not in ks]
    return el


KEEP_P = None     # turnover-matched placebo: probability a held name is kept (set from the variant's own run)


def random_ranker(rows, cfg, rng, held=None):
    el = [dict(r) for r in rows if SF.gate(r, cfg)]
    el = _shuffle_keep(el, rng, held, KEEP_P)
    for k, r in enumerate(el, 1):
        r["rank"] = k
    return el


def random_ranker_liq(rows, cfg, rng, held=None):
    el = [dict(r) for r in rows if r.get("liquid") and r["sym"] not in SF.MANUAL_ONLY]
    el = _shuffle_keep(el, rng, held, KEEP_P)
    for k, r in enumerate(el, 1):
        r["rank"] = k
    return el


def placebo(ctx, cfg, start, end, runs=200, seed=7, liq_only=False):
    rng = np.random.default_rng(seed)
    out = []
    for k in range(runs):
        res = simulate(ctx, cfg, start, end, ranker=random_ranker_liq if liq_only else random_ranker,
                       rng=rng, record=False)
        mt = metrics(res)
        out.append((mt["sharpe"], mt["total_return"], mt["max_dd"]))
    return out


def scaled_cfg(ctx, cfg, ref="2025-01-01"):
    """long-history control only: the 20 bn rial floor deflated by the market's median traded value
    (a nominal floor would leave < 10 stocks in 2018).  Disclosed deviation, P1 long run only."""
    return cfg


def run_variant(ctx, name, runs=200, out=None):
    cfg = SF.VARIANTS[name]
    res = {"variant": name, "config": asdict(cfg), "periods": {}}
    full = (PERIODS["IS"][0], PERIODS["OOS"][1])
    for per, (a, b) in list(PERIODS.items()) + [("FULL", full)]:
        r = simulate(ctx, cfg, a, b)
        mt = metrics(r)
        if per != "FULL" and runs:
            for tag, liq in (("placebo_gate", False), ("placebo_liq", True)):
                pl = placebo(ctx, cfg, a, b, runs=runs, liq_only=liq)
                sh = np.array([x[0] for x in pl]); tr = np.array([x[1] for x in pl])
                mt[tag] = dict(runs=runs, p_sharpe=round(float((sh >= mt["sharpe"]).mean()), 3),
                               p_return=round(float((tr >= mt["total_return"]).mean()), 3),
                               sharpe_med=round(float(np.median(sh)), 2), sharpe_p90=round(float(np.percentile(sh, 90)), 2),
                               ret_med=round(float(np.median(tr)), 1), ret_p90=round(float(np.percentile(tr, 90)), 1))
        res["periods"][per] = mt
        if per in ("IS", "OOS") and out:
            pd.DataFrame(r["trades"]).to_csv(Path(out) / f"trades_{name}_{per}.csv", index=False, encoding="utf-8-sig")
            pd.DataFrame(r["curve"], columns=["date", "equity"]).to_csv(Path(out) / f"curve_{name}_{per}.csv", index=False)
    return res


if __name__ == "__main__":
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "out"); outdir.mkdir(parents=True, exist_ok=True)
    names = [a for a in sys.argv[2:] if not a.startswith("--")] or list(SF.VARIANTS)
    runs = int(os.environ.get("PLACEBO_RUNS", 200))
    t0 = time.time()
    ctx = get_ctx()
    print("ctx", round(time.time() - t0), "s", ctx.F.cov, flush=True)
    for n in names:
        r = run_variant(ctx, n, runs=runs, out=outdir)
        json.dump(r, open(outdir / f"res_{n}.json", "w"), ensure_ascii=False, indent=1, default=float)
        print(n, json.dumps({k: {kk: v.get(kk) for kk in ("total_return", "sharpe", "max_dd", "trades", "win_rate")}
                             for k, v in r["periods"].items()}, ensure_ascii=False, default=float), round(time.time() - t0), flush=True)
