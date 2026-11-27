"""Robustness for F3 stop-loss variants: rebalance-phase offsets, slippage x3; price-only P1_MOM stop test
over 2018-2024 (contains the 2020-08..2021 TSE crash) as the only available bear-market check."""
import os, sys, pickle, json
from dataclasses import replace
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bt_fund as B, bt_fund_sl as S, strategy_fund as SF
out = Path(sys.argv[1]); part = sys.argv[2]
ctx = pickle.load(open("/workspace/work/f3sl_ctx.pkl", "rb"))
V = {n: (st, re) for n, st, re in S.VARS}
rows = []
def run(test, name, cfg, per, a, b):
    st, re = V[name]
    r = S.simulate_sl(ctx, cfg, a, b, stop=st, reentry=re); m = B.metrics(r); m.update(S.tail(r))
    rows.append(dict(test=test, variant=name, period=per, **{k: m.get(k) for k in ("total_return", "cagr", "max_dd", "sharpe", "trades", "win_rate", "worst_trade", "worst_month")}, triggers=r["stats"]["triggers"]))
    print(test, name, per, m["total_return"], m["max_dd"], r["stats"]["triggers"], flush=True)
if part == "f3":
    base = SF.VARIANTS["F3_QVGM"]
    names = ["baseline", "fixed10_replace", "fixed12_cash", "fixed12_replace", "fixed15_cash", "fixed15_replace", "trail20_replace"]
    for per, (a, b) in B.PERIODS.items():
        i0 = int(np.searchsorted(ctx.m.dates, np.datetime64(a)))
        for off in (5, 10, 15):
            a2 = str(pd.Timestamp(ctx.m.dates[i0 + off]).date())
            for n in names: run(f"start +{off}", n, base, per, a2, b)
        for n in names: run("slippage 0.75%", n, replace(base, slippage=0.0075), per, a, b)
else:
    p1 = replace(SF.VARIANTS["P1_MOM"], min_value_rial=-0.4)
    names = ["baseline", "fixed10_cash", "fixed10_replace", "fixed12_cash", "fixed12_replace", "fixed15_cash", "fixed15_replace", "trail15_replace", "trail20_cash", "trail20_replace"]
    for per, (a, b) in list(B.LONG.items()) + [("crash 2020-08..2021-06", ("2020-08-01", "2021-06-30"))]:
        for n in names: run("P1_MOM price-only", n, p1, per, a, b)
pd.DataFrame(rows).to_csv(out / f"robustness_{part}.csv", index=False)
