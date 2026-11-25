"""V3.1 study: pre-registered Codal quality filters on top of live V3 (same engine/fees/fill model)."""
import sys, json, time, math
from dataclasses import replace, asdict
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "/workspace/work/codal3/repo/warrobot"); sys.path.insert(0, "/workspace/work/codal3/research")
import backtest_v2 as B, backtest_v3 as BT, strategy_v3 as S3, codal_features as CF

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/work/codal3/research/out"); OUT.mkdir(parents=True, exist_ok=True)
PER = {"in_sample": ("2024-09-01", "2025-09-30"), "out_of_sample": ("2025-10-01", "2026-11-25")}
NEW = ("pe_c", "roe", "npm", "np_yoy", "sales_yoy", "capr_days")
t0 = time.time()
p = B.load_panel("/workspace/work/tsedata/panel.pkl"); m = B.Market(p)
z = np.load("/workspace/work/v3/out/_cache/features.npz", allow_pickle=True); F = {k: z[k] for k in z.files}
G = CF.build(m); cov = G.pop("coverage"); F.update(G)
codal_only = {k: F[k].copy() for k in ("pe_c", "roe", "np_yoy", "npm", "sales_yoy")}
F.update(CF.hybrid(F))
print("features", round(time.time() - t0), cov, flush=True)
_orig = BT.feat_row
def feat_row(F, i, j):
    r = _orig(F, i, j)
    for k in NEW: r[k] = float(F[k][i, j])
    return r
BT.feat_row = feat_row
v3 = S3.V3Config(fund_mode="exclude_loss", rank_by="bp")
V = {"V3_live": v3,
     "Q1_pe_c_gt100": replace(v3, pe_ttm_max=100.0),
     "Q2_pe_c_gt50": replace(v3, pe_ttm_max=50.0),
     "Q3_roe_lt5": replace(v3, roe_min=0.05),
     "Q4_roe_lt3": replace(v3, roe_min=0.03),
     "Q5_npm_lt5": replace(v3, npm_min=0.05),
     "Q6_np_yoy_lt-50": replace(v3, np_yoy_min=-0.5),
     "Q7_sales_yoy_lt0": replace(v3, sales_yoy_min=0.0),
     "Q8_capraise_reserve_120d": replace(v3, capraise_reserve_days=120),
     "Q9_guard_Q1+Q4+Q6": replace(v3, pe_ttm_max=100.0, roe_min=0.03, np_yoy_min=-0.5),
     "Q10_Q9+Q7": replace(v3, pe_ttm_max=100.0, roe_min=0.03, np_yoy_min=-0.5, sales_yoy_min=0.0)}
res = {"coverage": cov, "results": {}}
# coverage on V2-eligible signal rows
sig = {}
for k, (s, e) in PER.items():
    t = pd.read_csv(f"/workspace/work/wr-v3/reports/hark_backtest_v3/signals_{k}.csv.gz")
    di = {str(d)[:10]: i for i, d in enumerate(m.dates)}; sj = {str(x): j for j, x in enumerate(m.syms)}
    for c in NEW:
        t[c] = [F[c][di[d], sj[sy]] if d in di and sy in sj else np.nan for d, sy in zip(t.date, t.sym)]
    sig[k] = t; t.to_csv(OUT / f"signals_codal_{k}.csv.gz", index=False)
    res.setdefault("signal_coverage", {})[k] = {c: round(float(t[c].notna().mean() * 100), 1) for c in NEW} | {"n": int(len(t))}
    res.setdefault("signal_coverage_codal_only", {})[k] = {c: round(float(np.mean([not np.isnan(codal_only[c][di[d], sj[sy]]) if d in di and sy in sj else False for d, sy in zip(t.date, t.sym)]) * 100), 1) for c in codal_only}
RULES = {"Q1 pe_c>100|loss": lambda t: (t.pe_c > 100) | (t.pe_c <= 0), "Q2 pe_c>50|loss": lambda t: (t.pe_c > 50) | (t.pe_c <= 0),
         "Q3 roe<5%": lambda t: t.roe < 0.05, "Q4 roe<3%": lambda t: t.roe < 0.03, "Q5 npm<5%": lambda t: t.npm < 0.05,
         "Q6 np_yoy<-50%": lambda t: t.np_yoy < -0.5, "Q7 sales_yoy<0": lambda t: t.sales_yoy < 0,
         "Q8 capraise<=120d": lambda t: (t.capr_days >= 0) & (t.capr_days <= 120)}
COLS = {"Q1 pe_c>100|loss": "pe_c", "Q2 pe_c>50|loss": "pe_c", "Q3 roe<5%": "roe", "Q4 roe<3%": "roe", "Q5 npm<5%": "npm",
        "Q6 np_yoy<-50%": "np_yoy", "Q7 sales_yoy<0": "sales_yoy", "Q8 capraise<=120d": "capr_days"}
res["signal_study"] = []
for k, t in sig.items():
    t = t[t.filled & t.ret.notna()]
    t = t[~(t.pe <= 0)]                     # V3 already drops known loss-makers (rahavard)
    for rn, f in RULES.items():
        known = t[COLS[rn]].notna() if rn != "Q8 capraise<=120d" else pd.Series(True, index=t.index)
        ex = f(t).fillna(False) & known
        for lab, msk in (("excluded", ex), ("kept_known", known & ~ex), ("unknown", ~known)):
            u = t[msk]
            if len(u):
                res["signal_study"].append(dict(period=k, rule=rn, group=lab, n=int(len(u)), mean=round(u.ret.mean() * 100, 2),
                                                median=round(u.ret.median() * 100, 2), win=round((u.ret > 0).mean() * 100, 1)))
pd.DataFrame(res["signal_study"]).to_csv(OUT / "signal_study_v31.csv", index=False)
for name, cfg in V.items():
    res["results"][name] = {}
    for k, (s, e) in list(PER.items()) + [("full", ("2024-09-01", "2026-11-25"))]:
        r = BT.simulate(m, F, cfg, s, e)
        res["results"][name][k] = BT.metrics(r)
        if k == "full":
            pd.DataFrame(r["trades"]).to_csv(OUT / f"trades_{name}.csv", index=False)
    a, b_ = res["results"][name]["in_sample"], res["results"][name]["out_of_sample"]
    print(f"{name:28s} IS ret {a['total_return']:6.1f} dd {a['max_dd']:5.1f} n {a['trades']:4d} win {a.get('win_rate')} | OOS ret {b_['total_return']:6.1f} dd {b_['max_dd']:5.1f} n {b_['trades']:4d} win {b_.get('win_rate')}", flush=True)
json.dump(res, open(OUT / "results_v31.json", "w"), ensure_ascii=False, indent=1)
print("done", round(time.time() - t0))
