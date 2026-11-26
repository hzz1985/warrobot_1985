"""Robustness of the pre-registered main variant (not used for selection): capital, slippage, holdings,
rebalance-day offset, risk-free-adjusted Sharpe; price-only control over 2018-2024 (relative liquidity floor)."""
import json, sys, math
from dataclasses import replace
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bt_fund as B, strategy_fund as SF
REP = Path(sys.argv[1]); ctx = B.get_ctx(); base = SF.VARIANTS[sys.argv[2] if len(sys.argv) > 2 else "F3_QVGM"]
rows = []
def run(tag, cfg, per, a, b, cap=B.CAPITAL):
    r = B.simulate(ctx, cfg, a, b, capital=cap); m = B.metrics(r)
    eq = pd.Series([v for _, v in r["curve"]]); dr = eq.pct_change().dropna() - 0.30 / 240
    m["sharpe_rf30"] = round(float(dr.mean() / dr.std() * math.sqrt(240)), 2)
    rows.append(dict(test=tag, period=per, **{k: m.get(k) for k in ("total_return", "cagr", "max_dd", "sharpe", "sharpe_rf30", "trades", "win_rate")}))
for per, (a, b) in B.PERIODS.items():
    run("base", base, per, a, b)
    run("capital 100bn", base, per, a, b, cap=100e9)
    run("capital 500bn", base, per, a, b, cap=500e9)
    run("slippage 0.75%", replace(base, slippage=0.0075), per, a, b)
    run("n_hold 8", replace(base, n_hold=8, buffer=16), per, a, b)
    run("n_hold 12", replace(base, n_hold=12, buffer=24), per, a, b)
    run("no buffer", replace(base, buffer=base.n_hold), per, a, b)
    run("pe_max 25", replace(base, pe_max=25), per, a, b)
    run("liq floor 50bn", replace(base, min_value_rial=50e9), per, a, b)
    i0 = int(np.searchsorted(ctx.m.dates, np.datetime64(a)))
    for off in (5, 10, 15):
        run(f"start +{off} sessions", base, per, str(pd.Timestamp(ctx.m.dates[i0 + off]).date()), b)
p1 = replace(SF.VARIANTS["P1_MOM"], min_value_rial=-0.4)
for per, (a, b) in list(B.LONG.items()) + list(B.PERIODS.items()):
    run("P1_MOM top-40% liquidity", p1, per, a, b)
    m = B.ew_bench(ctx, a, b); rows.append(dict(test="EW index", period=per, **{k: m.get(k) for k in ("total_return", "cagr", "max_dd", "sharpe")}))
    t = B.tedpix(a, b); rows.append(dict(test="TEDPIX", period=per, **{k: t.get(k) for k in ("total_return", "cagr", "max_dd", "sharpe")}))
df = pd.DataFrame(rows); df.to_csv(REP / f"robustness_{base.variant}.csv", index=False)
print(df.to_string())
