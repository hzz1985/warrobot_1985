import sys, json, numpy as np
from dataclasses import replace
sys.path.insert(0, "/workspace/work/codal3/repo/warrobot"); sys.path.insert(0, "/workspace/work/codal3/research")
import backtest_v2 as B, backtest_v3 as BT, strategy_v3 as S3
p = B.load_panel("/workspace/work/tsedata/panel.pkl"); m = B.Market(p)
z = np.load("/workspace/work/v3/out/_cache/features.npz", allow_pickle=True); F = {k: z[k] for k in z.files}
_o = BT.feat_row
def fr(F, i, j):
    r = _o(F, i, j); r["pe_c"] = float(F["pe_c"][i, j]); return r
BT.feat_row = fr
cfg = replace(S3.V3Config(fund_mode="exclude_loss", rank_by="bp"), pe_ttm_max=100.0)
PER = {"in_sample": ("2024-09-01", "2025-09-30"), "out_of_sample": ("2025-10-01", "2026-11-25")}
out = {}
for rate in (0.05, 0.10):
    rs = {k: [] for k in PER}
    for seed in range(30):
        rng = np.random.default_rng(1000 + seed)
        F["pe_c"] = np.where(rng.random(m.c.shape) < rate, 1000.0, 10.0)
        for k, (s, e) in PER.items():
            x = BT.metrics(BT.simulate(m, F, cfg, s, e)); rs[k].append((x["total_return"], x["max_dd"]))
    out[rate] = {k: dict(ret_p10=float(np.percentile([a for a, b in v], 10)), ret_med=float(np.median([a for a, b in v])),
                          ret_p90=float(np.percentile([a for a, b in v], 90)), dd_med=float(np.median([b for a, b in v])))
                 for k, v in rs.items()}
    print(rate, out[rate], flush=True)
json.dump(out, open("/workspace/work/codal3/research/out/placebo.json", "w"), indent=1)
