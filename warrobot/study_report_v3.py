import pandas as pd, numpy as np, sys, json
sys.path.insert(0, '.')
from backtest_v3 import bucket_report
OUT = sys.argv[1] if len(sys.argv) > 1 else 'out'
periods = sys.argv[2].split(',') if len(sys.argv) > 2 else ['in_sample']
SPEC = [('pe', [-1e9, 0, 5, 10, 20, 30, 1e9]), ('growth', [-1e9, 0, 0.5, 1e9]),
        ('bp', [0, 1, 1.5, 2, 3, 1e9]), ('inflow', [-1e9, -0.1, 0, 0.1, 0.2, 1e9]),
        ('inflow5', [-1e9, -0.05, 0, 0.05, 0.1, 1e9]), ('sec_mom', [-1e9, -0.05, 0, 0.05, 0.1, 1e9]),
        ('vrank', [1, 7, 16, 31, 1e9])]
allrows = []
for k in periods:
    t = pd.read_csv(f'{OUT}/signals_{k}.csv.gz')
    f = t[t.filled & t.ret.notna()]
    print(f'== {k}: signals {len(t)}, filled {t.filled.mean():.2f}, traded {len(f)}, mean {f.ret.mean()*100:.2f}%, win {(f.ret>0).mean()*100:.1f}%')
    print('   coverage among signals: pe %.0f%%  growth %.0f%%  bp %.0f%%  sector %.0f%%' % tuple(t[c].notna().mean()*100 for c in ['pe','growth','bp','sec_mom']))
    for col, e in SPEC:
        for r in bucket_report(t, col, e):
            r['period'] = k; allrows.append(r)
            print('  ', r['feature'], r['bucket'], 'n', r['n'], 'mean', r['mean'], 'med', r['median'], 'win', r['win'], r['months_pos'])
pd.DataFrame(allrows).to_csv(f'{OUT}/feature_buckets_{"_".join(periods)}.csv', index=False)

# ---- within-day comparisons (each signal minus the mean of that day's signals) and daily top-6 picks
rng = np.random.default_rng(1)
summary = {}
for k in periods:
    t = pd.read_csv(f'{OUT}/signals_{k}.csv.gz')
    t['bpr'] = t.groupby('date').bp.rank(ascending=False, method='first')
    f = t[t.filled & t.ret.notna()].copy()
    f['dm'] = f.ret - f.groupby('date').ret.transform('mean')
    wd = {}
    for nm, mask in [('loss_making', f.pe <= 0), ('pe_0_10', (f.pe > 0) & (f.pe <= 10)), ('pe_gt_30', f.pe > 30),
                     ('growth_lt_0', f.growth < 0), ('bp_ge_1.5', f.bp >= 1.5), ('inflow_ge_0', f.inflow >= 0),
                     ('sector_mom_ge_0', f.sec_mom >= 0)]:
        wd[nm] = dict(n=int(mask.sum()), in_group=round(f[mask].dm.mean() * 100, 2), rest=round(f[~mask].dm.mean() * 100, 2))
    rnd = []
    for s in range(200):
        r = t.assign(u=rng.random(len(t)))
        r['rr'] = r.groupby('date').u.rank()
        rr = r[r.filled & r.ret.notna() & (r.rr <= 6)]
        rnd.append(rr.ret.mean())
    pick = lambda x: dict(n=int(len(x)), mean=round(x.ret.mean() * 100, 2), win=round((x.ret > 0).mean() * 100, 1))
    summary[k] = dict(within_day_pct=wd, top6_by_value=pick(f[f.vrank <= 6]), top6_by_bp=pick(f[f.bpr <= 6]),
                      random6=dict(mean=round(float(np.mean(rnd)) * 100, 2), p5=round(float(np.percentile(rnd, 5)) * 100, 2),
                                   p95=round(float(np.percentile(rnd, 95)) * 100, 2)),
                      all_signals=pick(f), coverage_pct={c: round(t[c].notna().mean() * 100, 1) for c in ['pe', 'growth', 'bp', 'sec_mom']})
    print(k, json.dumps(summary[k], ensure_ascii=False))
json.dump(summary, open(f'{OUT}/signal_study.json', 'w'), indent=1)
