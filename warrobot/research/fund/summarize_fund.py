"""Collect res_*.json + placebo files -> results_fund.json + table.md (gate verdict per variant)."""
import json, glob, sys, os
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
OUT = Path(sys.argv[1]); REP = Path(sys.argv[2]); REP.mkdir(parents=True, exist_ok=True)
import bt_fund as B
ctx = B.get_ctx()
bench = {per: {"EW_index": B.ew_bench(ctx, a, b), "TEDPIX": B.tedpix(a, b)} for per, (a, b) in B.PERIODS.items()}
bench["FULL"] = {"EW_index": B.ew_bench(ctx, B.PERIODS["IS"][0], B.PERIODS["OOS"][1]),
                 "TEDPIX": B.tedpix(B.PERIODS["IS"][0], B.PERIODS["OOS"][1])}
V3 = {"IS": dict(total_return=7.8, max_dd=18.9, win_rate=47.8, trades=90), "OOS": dict(total_return=155.0, max_dd=11.8, win_rate=69.6, trades=102)}


def pl(v, per, u, d):
    xs = []
    for f in glob.glob(str(OUT / d / f"{v}_{per}_{u}_*.json")):
        xs.extend(json.load(open(f)))
    return np.array(xs) if xs else None


res = {}
for f in sorted(glob.glob(str(OUT / "res_*.json"))):
    r = json.load(open(f)); v = r["variant"]
    for per in ("IS", "OOS"):
        mt = r["periods"][per]
        for d, tag in (("placebo", "naive"), ("placebo_tm", "turnover_matched")):
            src = v if d == "placebo_tm" else ("F3_QVGM" if v in ("F1_QV", "F2_QVG", "F7_QVGM_flow") else v)
            for u in ("gate", "liq"):
                a = pl(src, per, u, d)
                if a is None:
                    continue
                mt[f"p_{tag}_{u}"] = dict(n=len(a), p_sharpe=round(float((a[:, 0] >= mt["sharpe"]).mean()), 3),
                                          p_return=round(float((a[:, 1] >= mt["total_return"]).mean()), 3),
                                          med_sharpe=round(float(np.median(a[:, 0])), 2), med_return=round(float(np.median(a[:, 1])), 1),
                                          p90_return=round(float(np.percentile(a[:, 1], 90)), 1))
    g = {}
    I, O = r["periods"]["IS"], r["periods"]["OOS"]
    g["oos_return_pos"] = O["total_return"] > 0
    g["oos_sharpe_ge_1"] = O["sharpe"] >= 1
    g["maxdd_le_25_all"] = I["max_dd"] <= 25 and O["max_dd"] <= 25
    g["sharpe_gt_ew_both"] = I["sharpe"] > bench["IS"]["EW_index"]["sharpe"] and O["sharpe"] > bench["OOS"]["EW_index"]["sharpe"]
    pk = "p_turnover_matched_gate" if "p_turnover_matched_gate" in O else "p_naive_gate"
    pvals = [I.get(pk, {}).get("p_sharpe"), O.get(pk, {}).get("p_sharpe")]
    g["placebo_p_lt_0.1_both"] = all(p is not None and p < 0.1 for p in pvals)
    g["placebo_used"] = pk
    g["PASS"] = all(v for k, v in g.items() if k not in ("placebo_used",))
    r["gate"] = g
    res[v] = r
json.dump(dict(benchmarks=bench, live_v3=V3, variants=res), open(REP / "results_fund.json", "w"), ensure_ascii=False, indent=1, default=float)
L = ["| variant | period | ret % | CAGR % | maxDD % | Sharpe | trades | win % | avg win % | avg loss % | turnover x/yr | exposure % | placebo p (Sharpe, turnover-matched, gate univ.) | p (liq univ.) |",
     "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
for v, r in res.items():
    for per in ("IS", "OOS", "FULL"):
        m = r["periods"][per]
        pg = m.get("p_turnover_matched_gate", m.get("p_naive_gate", {})).get("p_sharpe", "") if per != "FULL" else ""
        pq = m.get("p_turnover_matched_liq", m.get("p_naive_liq", {})).get("p_sharpe", "") if per != "FULL" else ""
        L.append(f"| {v} | {per} | {m['total_return']} | {m['cagr']} | {m['max_dd']} | {m['sharpe']} | {m['trades']} | {m.get('win_rate')} | {m.get('avg_win')} | {m.get('avg_loss')} | {m.get('turnover_x_per_year')} | {m.get('exposure_pct')} | {pg} | {pq} |")
L.append("")
L.append("| benchmark | period | ret % | CAGR % | maxDD % | Sharpe |"); L.append("|---|---|---|---|---|---|")
for per, b in bench.items():
    for k, m in b.items():
        L.append(f"| {k} | {per} | {m['total_return']} | {m['cagr']} | {m['max_dd']} | {m['sharpe']} |")
L.append(f"| V3 live (prior report) | IS | 7.8 | | 18.9 | | 90 | 47.8 |"); L.append(f"| V3 live (prior report) | OOS | 155.0 | | 11.8 | | 102 | 69.6 |")
L.append(""); L.append("| variant | OOS ret>0 | OOS Sharpe>=1 | maxDD<=25 both | Sharpe>EW both | placebo p<0.1 both | PASS |"); L.append("|---|---|---|---|---|---|---|")
for v, r in res.items():
    g = r["gate"]
    L.append(f"| {v} | {g['oos_return_pos']} | {g['oos_sharpe_ge_1']} | {g['maxdd_le_25_all']} | {g['sharpe_gt_ew_both']} | {g['placebo_p_lt_0.1_both']} | **{g['PASS']}** |")
open(REP / "table.md", "w").write("\n".join(L) + "\n")
print("\n".join(L))
