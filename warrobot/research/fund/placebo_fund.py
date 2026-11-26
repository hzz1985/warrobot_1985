"""Random-portfolio placebo, resumable: same universe gate, schedule, buffer and execution as the variant,
only the ranking is random.  Writes OUT/placebo/<variant>_<period>_<universe>_<seed>.json (list of
[sharpe, total_return, max_dd]); run until each (variant, period, universe) has >= RUNS results.
python placebo_fund.py OUT RUNS BUDGET_S variant[,variant...]"""
import json, os, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bt_fund as B, strategy_fund as SF

MATCH = os.environ.get("MATCH_TURNOVER") == "1"
out = Path(sys.argv[1]) / ("placebo_tm" if MATCH else "placebo"); out.mkdir(parents=True, exist_ok=True)
RUNS, BUDGET = int(sys.argv[2]), float(sys.argv[3]); names = sys.argv[4].split(",")
t0 = time.time(); ctx = B.get_ctx()
for n in names:
    cfg = SF.VARIANTS[n]
    for per, (a, b) in B.PERIODS.items():
        if MATCH:
            B.KEEP_P = B.simulate(ctx, cfg, a, b, record=False)["retention"]
        for univ in (("gate",) if cfg.mode == "pead" or not cfg.pe_max else ("gate", "liq")):
            have = sum(len(json.load(open(f))) for f in out.glob(f"{n}_{per}_{univ}_*.json"))
            seed = 1000 + have
            res = []
            rng = np.random.default_rng(seed)
            while have + len(res) < RUNS and time.time() - t0 < BUDGET:
                r = B.simulate(ctx, cfg, a, b, ranker=B.random_ranker_liq if univ == "liq" else B.random_ranker, rng=rng, record=False)
                mt = B.metrics(r); res.append([mt["sharpe"], mt["total_return"], mt["max_dd"]])
            if res:
                json.dump(res, open(out / f"{n}_{per}_{univ}_{seed}.json", "w"))
            print(n, per, univ, have + len(res), 'keep_p', B.KEEP_P, flush=True)
