"""Backtest + honest validation of strategy V3 vs V2 (same engine, fees, queue-fill & floor-lock model
as backtest_v2.py).

Periods (fixed before looking at V3 results):
  in_sample      2024-09-01 .. 2025-09-30   (±3 % band)  -> thresholds chosen here only
  out_of_sample  2025-10-01 .. 2026-11-25   (±3 % band)  -> touched once with the frozen variants
  pre_5pct       2022-07-01 .. 2024-08-31   (±5 % band)  -> money-flow only (no PIT fundamentals that far back)
Data gap: no prices 2026-02-25 .. 2026-05-19 (positions are carried through it).

Usage: python backtest_v3.py --panel /workspace/work/tsedata/panel.pkl --index .../tedpix_tgju.csv --out ../reports/hark_backtest_v3
"""
from __future__ import annotations

import argparse, json, math, os, sys, time
from dataclasses import replace, asdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import strategy_v2 as S2           # noqa: E402
import strategy_v3 as S3           # noqa: E402
import backtest_v2 as B            # noqa: E402
import features_v3 as FV           # noqa: E402

PERIODS = {"in_sample": ("2024-09-01", "2025-09-30"),
           "out_of_sample": ("2025-10-01", "2026-11-25"),
           "pre_5pct": ("2022-07-01", "2024-08-31")}


def feat_row(F, i, j):
    g = lambda k: float(F[k][i, j]) if k in F else float("nan")
    return dict(pe=g("pe"), growth=g("growth"), bp=g("bp"), inflow=g("inflow"), inflow5=g("inflow5"),
                sec_mom=g("sec_mom"))


# ------------------------------------------------------------------ portfolio simulation
def simulate(m, F, cfg: S3.V3Config, start, end, seed=7):
    rng = np.random.default_rng(seed)
    i0 = int(np.searchsorted(m.dates, np.datetime64(start)))
    i1 = int(np.searchsorted(m.dates, np.datetime64(end), side="right"))
    reg = m.regime_ok(cfg.regime_ma)
    bf, sf, slip = S2.BUY_FEE, S2.SELL_FEE, cfg.slippage
    cash, pos, pending, trades, curve, expo = 1.0, {}, [], [], [], []
    for i in range(i0, i1):
        for od in pending:
            j = od["j"]
            if not m.traded[i, j]:
                cash += od["alloc"] * (1 + bf); continue
            py, o, l, c, L = m.py[i, j], m.o[i, j], m.l[i, j], m.c[i, j], m.L[i, j]
            ce = py * (1 + L); px = None
            if cfg.entry == "queue":
                if o < ce * 0.999: px = o
                elif l < ce * 0.999: px = ce
            elif cfg.entry == "open_unlocked":
                if o < ce * 0.999: px = o * (1 + slip)
            if px is None:
                cash += od["alloc"] * (1 + bf); continue
            r = c / px
            pos[j] = dict(alloc=od["alloc"], r=r, peak=max(r, 1.0), held=0, entry_i=i, sig_i=od["sig_i"], f=od["f"])
        pending = []
        for j in list(pos):
            ps = pos[j]
            if ps["entry_i"] == i or not m.traded[i, j]:
                continue
            bar = dict(py=m.py[i, j], o=m.o[i, j], h=m.h[i, j], l=m.l[i, j], c=m.c[i, j])
            reason, x = S2.exit_decision(cfg, ps["r"], ps["peak"], ps["held"], bar, m.L[i, j])
            if reason:
                if reason != "tp": x *= (1 - slip)
                proceeds = ps["alloc"] * x * (1 - sf); cash += proceeds
                trades.append(dict(sym=str(m.syms[j]), sig=str(m.dates[ps["sig_i"]])[:10],
                                   entry=str(m.dates[ps["entry_i"]])[:10], exit=str(m.dates[i])[:10],
                                   reason=reason, ret=float(proceeds / (ps["alloc"] * (1 + bf)) - 1),
                                   held=ps["held"] + 1, **{k: (None if (isinstance(v, float) and math.isnan(v)) else round(v, 4)) for k, v in ps["f"].items()}))
                del pos[j]; continue
            ps["r"] *= bar["c"] / bar["py"]; ps["peak"] = max(ps["peak"], ps["r"]); ps["held"] += 1
        inv = sum(p["alloc"] * p["r"] for p in pos.values())
        eq = cash + inv
        curve.append((str(m.dates[i])[:10], eq)); expo.append(inv / eq if eq > 0 else 0)
        if i + 1 >= i1:
            continue
        free = cfg.max_positions - len(pos)
        if free <= 0:
            continue
        rows = []
        for j in np.where(m.up[i] & m.traded[i])[0]:
            rows.append(dict(sym=j, lock_up=True, prev_lock_up=bool(m.prev_up[i, j]), value=float(m.val[i, j]),
                             floor_lock_recent=bool(m.dn_recent[i, j]), **feat_row(F, i, j)))
        for r in S3.pick_candidates(rows, cfg, bool(reg[i]), set(pos))[:free]:
            alloc = min(eq / cfg.max_positions, cash / (1 + bf))
            if alloc <= eq * 0.02:
                break
            cash -= alloc * (1 + bf)
            pending.append(dict(j=r["sym"], alloc=alloc, sig_i=i, f={k: r[k] for k in ("pe", "growth", "bp", "inflow", "inflow5", "sec_mom")}))
    return dict(curve=curve, trades=trades, exposure=float(np.mean(expo)) if expo else 0.0)


def metrics(res):
    out = B.metrics(res)
    t = pd.DataFrame(res["trades"])
    if len(t):
        w, l = t.ret[t.ret > 0], t.ret[t.ret <= 0]
        out.update(avg_win=round(float(w.mean() * 100), 2) if len(w) else None,
                   avg_loss=round(float(l.mean() * 100), 2) if len(l) else None,
                   expectancy=round(float(t.ret.mean() * 100), 2),
                   payoff=round(float(w.mean() / -l.mean()), 2) if len(w) and len(l) and l.mean() < 0 else None)
    out["exposure"] = round(res.get("exposure", 0) * 100, 1)
    return out


def ew_bh(m, start, end):
    i0 = int(np.searchsorted(m.dates, np.datetime64(start)))
    i1 = int(np.searchsorted(m.dates, np.datetime64(end), side="right"))
    s = pd.Series(m.ew[i0:i1], index=pd.to_datetime(m.dates[i0:i1]))
    s = s / s.iloc[0]
    days = (s.index[-1] - s.index[0]).days
    dr = s.pct_change().dropna()
    return dict(start=str(s.index[0].date()), end=str(s.index[-1].date()),
                total_return=round((s.iloc[-1] - 1) * 100, 1),
                cagr=round((s.iloc[-1] ** (365.25 / days) - 1) * 100, 1),
                max_dd=round(float((1 - s / s.cummax()).max()) * 100, 1),
                sharpe=round(float(dr.mean() / dr.std() * math.sqrt(240)), 2),
                note="equal-weight all stocks, daily rebalanced, no fees (upper bound of a tradable B&H)")


# ------------------------------------------------------------------ independent signal study
def signal_table(m, F, cfg: S2.V2Config, start, end):
    """Every V2-eligible signal traded independently with V2 entry/exit -> one row with its features."""
    i0 = int(np.searchsorted(m.dates, np.datetime64(start)))
    i1 = int(np.searchsorted(m.dates, np.datetime64(end), side="right"))
    reg = m.regime_ok(cfg.regime_ma)
    out = []
    for i in range(i0, i1 - 1):
        if cfg.regime_ma and not reg[i]:
            continue
        cand = np.where(m.up[i] & m.traded[i] & (m.val[i] >= cfg.min_value_rial) & ~m.dn_recent[i])[0]
        if not len(cand):
            continue
        vr = pd.Series(-m.val[i, cand]).rank().to_numpy()
        for jj, j in enumerate(cand):
            k = i + 1
            row = dict(i=i, date=str(m.dates[i])[:10], sym=str(m.syms[j]), val=float(m.val[i, j]),
                       vrank=int(vr[jj]), n_sig=len(cand), **feat_row(F, i, j), filled=False, ret=np.nan)
            if m.traded[k, j]:
                py, o, l, c, L = m.py[k, j], m.o[k, j], m.l[k, j], m.c[k, j], m.L[k, j]
                ce = py * (1 + L)
                px = o if o < ce * 0.999 else (ce if l < ce * 0.999 else None)
                if px is not None:
                    row["filled"] = True
                    r, peak, held = c / px, max(c / px, 1.0), 0
                    for q in range(k + 1, len(m.dates)):
                        if not m.traded[q, j]:
                            continue
                        bar = dict(py=m.py[q, j], o=m.o[q, j], h=m.h[q, j], l=m.l[q, j], c=m.c[q, j])
                        reason, x = S2.exit_decision(cfg, r, peak, held, bar, m.L[q, j])
                        if reason:
                            row["ret"] = x * (1 - cfg.slippage) * (1 - S2.SELL_FEE) / (1 + S2.BUY_FEE) - 1
                            break
                        r *= bar["c"] / bar["py"]; peak = max(peak, r); held += 1
            out.append(row)
    return pd.DataFrame(out)


def bucket_report(t: pd.DataFrame, col: str, edges, label=None):
    t = t[t.filled & t.ret.notna()]
    rows = []
    for name, mask in [("unknown", t[col].isna())] + [
            (f"[{lo},{hi})", (t[col] >= lo) & (t[col] < hi)) for lo, hi in zip(edges[:-1], edges[1:])]:
        s = t[mask]
        if len(s) == 0:
            continue
        mm = s.groupby(s.date.str[:7]).ret.mean()
        rows.append(dict(feature=label or col, bucket=name, n=int(len(s)), mean=round(s.ret.mean() * 100, 2),
                         median=round(s.ret.median() * 100, 2), win=round((s.ret > 0).mean() * 100, 1),
                         months_pos=f"{int((mm > 0).sum())}/{len(mm)}"))
    return rows


# ------------------------------------------------------------------ variants (pre-registered list)
def variants():
    v2 = S3.V3Config(fund_mode="off", rank_by="value")   # == V2_recommended (live V2)
    return {
        "V2_baseline": v2,
        "F1_exclude_loss": replace(v2, fund_mode="exclude_loss"),
        "F2_profit_pe_le_30": replace(v2, fund_mode="require_profit", pe_max=30.0),
        "F3_exclude_loss_growth_ge_0": replace(v2, fund_mode="exclude_loss", growth_min=0.0),
        "M1_bp_ge_1.5": replace(v2, bp_min=1.5),
        "M2_inflow_ge_0": replace(v2, inflow_min=0.0),
        "M3_rank_by_bp": replace(v2, rank_by="bp"),
        "S1_sector_mom_ge_0": replace(v2, sec_mom_min=0.0),
        "C1_exclude_loss+bp1.5": replace(v2, fund_mode="exclude_loss", bp_min=1.5),
        "C2_exclude_loss+pe30+bp1.5+sector": replace(v2, fund_mode="exclude_loss", pe_max=30.0, bp_min=1.5, sec_mom_min=0.0),
        # added after the 10 above were run: the IS winner (rank by buyer power) + the fundamental guard that had
        # the clearest within-day IS effect (known loss-makers -2.2 %/trade).  This is the proposed V3.
        "V3_rankbp_exclude_loss": replace(v2, fund_mode="exclude_loss", rank_by="bp"),
    }


def random_mc(m, F, cfg, start, end, runs=40):
    """Same filters, random order among eligible candidates: where does a ranking rule sit vs luck?"""
    orig = S3.pick_candidates
    out = []
    for s in range(runs):
        rng = np.random.default_rng(500 + s)

        def rnd(rows, c, ok, held, _o=orig, _r=rng):
            r = _o(rows, replace(c, max_picks=10 ** 6), ok, held)
            _r.shuffle(r)
            return r[: c.max_picks]
        S3.pick_candidates = rnd
        try:
            out.append(metrics(simulate(m, F, cfg, start, end)))
        finally:
            S3.pick_candidates = orig
    tr = np.array([x["total_return"] for x in out]); wr = np.array([x.get("win_rate", np.nan) for x in out])
    return dict(runs=runs, total_p5=round(float(np.percentile(tr, 5)), 1), total_median=round(float(np.median(tr)), 1),
                total_p95=round(float(np.percentile(tr, 95)), 1), win_median=round(float(np.nanmedian(wr)), 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="/workspace/work/tsedata/panel.pkl")
    ap.add_argument("--index", default="/workspace/work/tsedata/tedpix_tgju.csv")
    ap.add_argument("--out", default=str(HERE.parent / "reports" / "hark_backtest_v3"))
    ap.add_argument("--mc", type=int, default=40)
    ap.add_argument("--stage", default="all", choices=["features", "study", "variants", "all"])
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    cache = out / "_cache"; cache.mkdir(exist_ok=True)
    t0 = time.time()
    p = B.load_panel(a.panel)
    m = B.Market(p)
    fc = cache / "features.npz"
    if fc.exists() and a.stage not in ("features",):
        z = np.load(fc, allow_pickle=True); F = {k: z[k] for k in z.files}
    else:
        F = FV.build(m, p)
        np.savez_compressed(fc, **{k: v for k, v in F.items() if isinstance(v, np.ndarray)})
        json.dump({k: int(v) for k, v in F.items() if k.startswith("coverage")}, open(out / "coverage_raw.json", "w"))
    print("features", round(time.time() - t0), flush=True)
    if a.stage == "features":
        return
    base = S2.V2Config()
    if a.stage in ("study", "all"):
        tabs = {}
        for k, (s, e) in PERIODS.items():
            tabs[k] = signal_table(m, F, base, s, e)
            tabs[k].to_csv(out / f"signals_{k}.csv.gz", index=False)
        print("study", round(time.time() - t0), flush=True)
    if a.stage in ("variants", "all"):
        idx = pd.read_csv(a.index, parse_dates=["date"])
        res = {"periods": PERIODS, "results": {}, "index": {}, "ew": {}}
        for k, (s, e) in PERIODS.items():
            try:
                res["index"][k] = B.index_bh(idx, s, e)
            except Exception as ex:
                res["index"][k] = {"error": str(ex)}
            res["ew"][k] = ew_bh(m, s, e)
        full = ("2024-09-01", "2026-11-25")
        res["index"]["full"] = B.index_bh(idx, *full); res["ew"]["full"] = ew_bh(m, *full)
        for name, cfg in variants().items():
            res["results"][name] = {"config": asdict(cfg)}
            for k, (s, e) in list(PERIODS.items()) + [("full", full)]:
                r = simulate(m, F, cfg, s, e)
                res["results"][name][k] = metrics(r)
                if k == "full":
                    pd.DataFrame(r["trades"]).to_csv(out / f"trades_{name}.csv", index=False)
                    (out / "curves").mkdir(exist_ok=True)
                    pd.DataFrame(r["curve"], columns=["date", "equity"]).to_csv(out / "curves" / f"curve_{name}.csv", index=False)
            print(name, res["results"][name]["in_sample"].get("cagr"), res["results"][name]["out_of_sample"].get("cagr"), flush=True)
        res["random_pick_mc"] = {}
        for nm, cfg in [("V2_filters_random_order", S3.V3Config(fund_mode="off")),
                        ("V3_filters_random_order", S3.V3Config(fund_mode="exclude_loss"))]:
            res["random_pick_mc"][nm] = {k: random_mc(m, F, cfg, s, e, runs=a.mc) for k, (s, e) in PERIODS.items()}
            print(nm, res["random_pick_mc"][nm], flush=True)
        json.dump(res, open(out / "results.json", "w"), ensure_ascii=False, indent=1)
    print("done", round(time.time() - t0))


if __name__ == "__main__":
    main()
