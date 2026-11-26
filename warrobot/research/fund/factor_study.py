"""Per-factor evidence: at each monthly signal day, Spearman IC and top-minus-bottom quintile of the next
21-session total return (gate-G universe), IS and OOS separately.  Not used to choose anything."""
import sys, math, json
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bt_fund as B, strategy_fund as SF
REP = Path(sys.argv[1]); ctx = B.get_ctx(); m = ctx.m; cfg = SF.VARIANTS["F3_QVGM"]
out = []
for per, (a, b) in B.PERIODS.items():
    i0 = int(np.searchsorted(m.dates, np.datetime64(a))); i1 = int(np.searchsorted(m.dates, np.datetime64(b), side="right")) - 1
    for i in range(i0, i1 - 22, 21):
        rows = [r for r in B.rows_at(ctx, i, cfg) if SF.gate(r, cfg)]
        for r in rows:
            r["ey"] = r["np_ttm"] / r["mcap"]
            j = r["j"]; r["fwd"] = m.tr[i + 22, j] / m.tr[i + 1, j] - 1
        df = pd.DataFrame(rows)
        for f in ("ey", "yoy", "roe", "mom"):
            d = df[["fwd", f]].dropna()
            if len(d) < 30: continue
            ic = d[f].rank().corr(d.fwd.rank())
            q = pd.qcut(d[f].rank(method="first"), 5, labels=False)
            out.append(dict(period=per, date=ctx.dstr[i], factor=f, n=len(d), ic=ic,
                            q5_minus_q1=d.fwd[q == 4].mean() - d.fwd[q == 0].mean()))
df = pd.DataFrame(out)
s = df.groupby(["period", "factor"]).agg(months=("ic", "size"), n_avg=("n", "mean"), ic_mean=("ic", "mean"),
                                          ic_t=("ic", lambda x: x.mean() / x.std() * math.sqrt(len(x)) if len(x) > 1 else np.nan),
                                          q5_q1_monthly_pct=("q5_minus_q1", lambda x: x.mean() * 100),
                                          ic_pos_share=("ic", lambda x: (x > 0).mean())).round(3)
s.to_csv(REP / "factor_study.csv"); print(s.to_string())
