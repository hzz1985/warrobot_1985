"""Realistic portfolio backtest of the ceiling-lock strategy (v1) and v2 variants.

Data  : tsetmc daily history for every TSE + Farabourse stock (incl. delisted
        instruments / old insCodes), built by fetch_tsetmc_history.py into a
        pickle panel  (columns: sym, board, date, py, o, l, h, c, last, ntr, vol, val).
Index : TEDPIX (شاخص کل) daily close from api.tgju.org (CSV: date, close).

Realism (no look-ahead):
  * signal on the close of session t, order for session t+1
  * daily price limit L inferred per date & board from the data (3 % since 2024-09)
  * queue entry (v1): a pre-open bid at the t+1 ceiling fills only if t+1 opens below
    the ceiling (filled at the open) or trades below it later (queue broke -> ceiling).
    A session locked at the ceiling all day fills with probability `fill_locked_prob`.
  * exits start the session after the fill; a session locked at the floor all day is
    unsellable; stop gaps fill at the open; stop/TP intrabar: stop first
  * fees buy 0.3712 %, sell 0.88 % (+ slippage 0.25 % on market-style fills)
  * corporate actions handled by measuring every session against its own reference
    price (total-return; cash dividends credited at the ex-date)
  * positions in instruments that stop trading stay marked at their last price

Usage:
  python backtest_v2.py --panel PATH/panel.pkl --index PATH/tedpix.csv --out ../reports/hark_backtest
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import strategy_v2 as S

LIMIT_LEVELS = (0.07, 0.06, 0.05, 0.04, 0.03, 0.02)


# ------------------------------------------------------------------ data prep
def load_panel(path: str) -> pd.DataFrame:
    p = pd.read_pickle(path) if path.endswith(".pkl") else pd.read_csv(path, parse_dates=["date"])
    p = p[p.board.isin([1, 2]) & (p.ntr > 0) & (p.py > 0) & (p.h > 0) & (p.c > 0)].copy()
    p["ru"] = p.h / p.py - 1

    def lim(s):
        for k in LIMIT_LEVELS:
            if ((s - k).abs() < 0.0015).sum() >= 3:
                return k
        return np.nan

    L = p.groupby(["date", "board"]).ru.apply(lim).rename("L").reset_index()
    p = p.merge(L, on=["date", "board"], how="left")
    p["L"] = p["L"].fillna(0.05)
    p["o"] = p["o"].where(p["o"] > 0, p["c"])
    within = p.h <= p.py * (1 + p.L) * 1.003          # excludes IPO / no-limit reopen days
    p["lock_up"] = within & (p["last"] >= p.h) & (p.h >= p.py * (1 + p.L) * 0.997) & \
                   (p.c >= p.py * (1 + p.L) * (1 - 0.0025) * 0.999)
    p["lock_dn"] = (p["last"] <= p.l) & (p.l <= p.py * (1 - p.L) * 1.003)
    return p.sort_values(["sym", "date"]).reset_index(drop=True)


class Market:
    """Dense date x symbol matrices."""

    def __init__(self, p: pd.DataFrame):
        self.dates = np.array(sorted(p.date.unique()))
        self.syms = np.array(sorted(p.sym.unique()))
        di = {d: i for i, d in enumerate(self.dates)}
        si = {s: j for j, s in enumerate(self.syms)}
        I = p.date.map(di).to_numpy()
        J = p.sym.map(si).to_numpy()
        shape = (len(self.dates), len(self.syms))

        def mat(col, fill=np.nan, dtype=float):
            m = np.full(shape, fill, dtype=dtype)
            m[I, J] = p[col].to_numpy()
            return m

        self.py, self.o, self.h, self.l, self.c = (mat(k) for k in ("py", "o", "h", "l", "c"))
        self.last, self.val, self.L = mat("last"), mat("val", 0.0), mat("L")
        self.up = mat("lock_up", False, bool)
        self.dn = mat("lock_dn", False, bool)
        self.traded = ~np.isnan(self.c)
        # previous traded session lock-up (per symbol), floor-lock in last 10 traded sessions
        self.prev_up = np.zeros(shape, bool)
        self.dn_recent = np.zeros(shape, bool)
        for j in range(shape[1]):
            idx = np.where(self.traded[:, j])[0]
            if len(idx) < 2:
                continue
            u = self.up[idx, j]
            self.prev_up[idx[1:], j] = u[:-1]
            d = self.dn[idx, j].astype(int)
            cs = np.cumsum(d)
            lag = np.concatenate([np.zeros(10, int), cs[:-10]]) if len(cs) > 10 else np.zeros(len(cs), int)
            self.dn_recent[idx, j] = (cs - lag) > 0
        # equal-weight breadth index (all traded stocks, session return vs reference)
        r = np.where(self.traded, self.c / self.py - 1, np.nan)
        r = np.clip(r, -0.25, 0.25)
        self.ew_ret = np.nan_to_num(np.nanmean(r, axis=1))
        self.ew = np.cumprod(1 + self.ew_ret)
        self.breadth = np.nanmean(np.where(self.traded, (self.c > self.py).astype(float), np.nan), axis=1)

    def regime_ok(self, ma: int) -> np.ndarray:
        if not ma:
            return np.ones(len(self.dates), bool)
        s = pd.Series(self.ew).rolling(ma, min_periods=ma).mean().to_numpy()
        return np.nan_to_num(self.ew > s, nan=0).astype(bool)


# ------------------------------------------------------------------ simulation
def simulate(m: Market, cfg: S.V2Config, start, end, naive: bool = False,
             fill_locked_prob: float = 0.0, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    i0 = int(np.searchsorted(m.dates, np.datetime64(start)))
    i1 = int(np.searchsorted(m.dates, np.datetime64(end), side="right"))
    reg = m.regime_ok(cfg.regime_ma)
    buy_fee, sell_fee = (0.0, 0.02) if naive else (S.BUY_FEE, S.SELL_FEE)
    slip = 0.0 if naive else cfg.slippage

    cash, positions, pending, trades, curve = 1.0, {}, [], [], []
    for i in range(i0, i1):
        # 1) fills of orders placed at the previous close
        for od in pending:
            j = od["j"]
            if not m.traded[i, j]:
                cash += od["alloc"] * (1 + buy_fee)
                continue
            py, o, l, h, c, L = m.py[i, j], m.o[i, j], m.l[i, j], m.h[i, j], m.c[i, j], m.L[i, j]
            px = None
            if naive:                                   # replica of walkforward.py / portfolio_sim.py
                px = od["sig_ceil"] if h >= od["sig_ceil"] else None
            elif cfg.entry == "queue":
                ce = py * (1 + L)
                if o < ce * 0.999:
                    px = o
                elif l < ce * 0.999:
                    px = ce
                elif rng.random() < fill_locked_prob:
                    px = ce
            elif cfg.entry == "open_unlocked":
                ce = py * (1 + L)
                if o < ce * 0.999:
                    px = o * (1 + slip)
            if px is None:
                cash += od["alloc"] * (1 + buy_fee)
                continue
            r = c / px
            positions[j] = dict(j=j, alloc=od["alloc"], r=r, peak=max(r, 1.0), held=0,
                                entry_i=i, px=px, sig_i=od["sig_i"])
        pending = []

        # 2) exits for positions opened before today
        for j in list(positions):
            ps = positions[j]
            if ps["entry_i"] == i or not m.traded[i, j]:
                continue
            bar = dict(py=m.py[i, j], o=m.o[i, j], h=m.h[i, j], l=m.l[i, j], c=m.c[i, j])
            L = m.L[i, j]
            if naive:
                k = ps["r"] / bar["py"]
                reason, x = None, None
                if bar["h"] * k >= 1 + cfg.tp:
                    reason, x = "tp", 1 + cfg.tp
                elif bar["l"] * k <= 1 - cfg.sl_fixed:
                    reason, x = "stop", 1 - cfg.sl_fixed
                elif ps["held"] + 1 >= cfg.max_hold:
                    reason, x = "time", bar["c"] * k
            else:
                reason, x = S.exit_decision(cfg, ps["r"], ps["peak"], ps["held"], bar, L)
            if reason:
                if reason != "tp":
                    x *= (1 - slip)
                proceeds = ps["alloc"] * x * (1 - sell_fee)
                cash += proceeds
                ret = proceeds / (ps["alloc"] * (1 + buy_fee)) - 1
                trades.append(dict(sym=str(m.syms[j]), sig=str(m.dates[ps["sig_i"]])[:10],
                                   entry=str(m.dates[ps["entry_i"]])[:10],
                                   exit=str(m.dates[i])[:10], reason=reason,
                                   ret=float(ret), held=ps["held"] + 1))
                del positions[j]
                continue
            ps["r"] = ps["r"] * bar["c"] / bar["py"]
            ps["peak"] = max(ps["peak"], ps["r"])
            ps["held"] += 1

        equity = cash + sum(ps["alloc"] * ps["r"] for ps in positions.values())
        curve.append((str(m.dates[i])[:10], equity))

        # 3) new orders for tomorrow
        if i + 1 >= i1:
            continue
        free = cfg.max_positions - len(positions)
        if free <= 0:
            continue
        rows = []
        cand = np.where(m.up[i] & m.traded[i])[0]
        for j in cand:
            rows.append(dict(sym=j, lock_up=True, prev_lock_up=bool(m.prev_up[i, j]),
                             value=float(m.val[i, j]), floor_lock_recent=bool(m.dn_recent[i, j])))
        picks = S.pick_candidates(rows, cfg, bool(reg[i]), set(positions))
        for r in picks[:free]:
            alloc = min(equity / cfg.max_positions, cash / (1 + buy_fee))
            if alloc <= equity * 0.02:
                break
            cash -= alloc * (1 + buy_fee)
            j = r["sym"]
            pending.append(dict(j=j, alloc=alloc, sig_i=i,
                                sig_ceil=m.py[i, j] * (1 + m.L[i, j])))
    for od in pending:
        cash += od["alloc"] * (1 + buy_fee)
    return dict(curve=curve, trades=trades)


# ------------------------------------------------------------------ event study
def event_study(m: Market, cfg: S.V2Config, start, end, fill_locked_prob: float = 0.0,
                seed: int = 7, control: int = 0) -> dict:
    """Every signal traded independently (no slot limit): large-sample edge estimate."""
    rng = np.random.default_rng(seed)
    i0 = int(np.searchsorted(m.dates, np.datetime64(start)))
    i1 = int(np.searchsorted(m.dates, np.datetime64(end), side="right"))
    reg = m.regime_ok(cfg.regime_ma)
    n_sig = n_fill = 0
    rets, by_month = [], {}
    for i in range(i0, i1 - 1):
        if cfg.regime_ma and not reg[i]:
            continue
        if control:                                    # random liquid stock-days, same exits
            pool = np.where(m.traded[i] & (m.val[i] >= cfg.min_value_rial))[0]
            cand = rng.choice(pool, size=min(len(pool), control), replace=False) if len(pool) else pool
        else:
            cand = np.where(m.up[i] & m.traded[i] & (m.val[i] >= cfg.min_value_rial))[0]
        if cfg.first_lock_only and not control:
            cand = cand[~m.prev_up[i, cand]]
        if cfg.avoid_floor_lock_days:
            cand = cand[~m.dn_recent[i, cand]]
        for j in cand:
            n_sig += 1
            k = i + 1
            if not m.traded[k, j]:
                continue
            py, o, l, c, L = m.py[k, j], m.o[k, j], m.l[k, j], m.c[k, j], m.L[k, j]
            ce = py * (1 + L)
            if control:
                px = o * (1 + cfg.slippage)
            elif cfg.entry == "queue":
                px = o if o < ce * 0.999 else (ce if l < ce * 0.999 else
                                               (ce if rng.random() < fill_locked_prob else None))
            else:
                px = o * (1 + cfg.slippage) if o < ce * 0.999 else None
            if px is None:
                continue
            n_fill += 1
            r, peak, held, ret = c / px, max(c / px, 1.0), 0, None
            for q in range(k + 1, i1):
                if not m.traded[q, j]:
                    continue
                bar = dict(py=m.py[q, j], o=m.o[q, j], h=m.h[q, j], l=m.l[q, j], c=m.c[q, j])
                reason, x = S.exit_decision(cfg, r, peak, held, bar, m.L[q, j])
                if reason:
                    if reason != "tp":
                        x *= (1 - cfg.slippage)
                    ret = x * (1 - S.SELL_FEE) / (1 + S.BUY_FEE) - 1
                    break
                r = r * bar["c"] / bar["py"]
                peak = max(peak, r)
                held += 1
            if ret is None:
                continue                               # still open at period end
            rets.append(ret)
            mo = str(m.dates[i])[:7]
            by_month.setdefault(mo, []).append(ret)
    a = np.array(rets)
    if not len(a):
        return dict(signals=n_sig, fills=n_fill, trades=0)
    mm = np.array([np.mean(v) for v in by_month.values()])
    return dict(signals=n_sig, fills=n_fill, fill_rate=round(n_fill / max(n_sig, 1), 3), trades=int(len(a)),
                mean_net=round(float(a.mean() * 100), 2), median_net=round(float(np.median(a) * 100), 2),
                win_rate=round(float((a > 0).mean() * 100), 1),
                t_stat_monthly=round(float(mm.mean() / (mm.std(ddof=1) / np.sqrt(len(mm)))), 2) if len(mm) > 2 else None,
                months_positive=f"{int((mm > 0).sum())}/{len(mm)}")


def random_pick_mc(m: Market, cfg: S.V2Config, start, end, runs: int = 100) -> dict:
    """Same rules, but the daily pick among eligible candidates is random: shows how
    much of a portfolio result is luck of the turnover ranking."""
    orig = S.pick_candidates
    out = []
    for s in range(runs):
        rng = np.random.default_rng(1000 + s)

        def rnd(rows, c, ok, held, _o=orig, _r=rng):
            r = _o(rows, replace(c, max_picks=10 ** 6), ok, held)
            _r.shuffle(r)
            return r[: c.max_picks]
        S.pick_candidates = rnd
        try:
            out.append(metrics(simulate(m, cfg, start, end)))
        finally:
            S.pick_candidates = orig
    cg = np.array([x["cagr"] for x in out])
    dd = np.array([x["max_dd"] for x in out])
    return dict(runs=runs, cagr_p5=round(float(np.percentile(cg, 5)), 1),
                cagr_median=round(float(np.median(cg)), 1), cagr_p95=round(float(np.percentile(cg, 95)), 1),
                maxdd_median=round(float(np.median(dd)), 1), share_positive=round(float((cg > 0).mean()), 2))


# ------------------------------------------------------------------ metrics
def metrics(res: dict) -> dict:
    eq = pd.Series([v for _, v in res["curve"]], index=pd.to_datetime([d for d, _ in res["curve"]]))
    if len(eq) < 2:
        return {}
    days = (eq.index[-1] - eq.index[0]).days or 1
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (365.25 / days) - 1
    dr = eq.pct_change().dropna()
    sharpe = float(dr.mean() / dr.std() * math.sqrt(240)) if dr.std() > 0 else 0.0
    mdd = float((1 - eq / eq.cummax()).max())
    t = pd.DataFrame(res["trades"])
    out = dict(start=str(eq.index[0].date()), end=str(eq.index[-1].date()),
               total_return=round(total * 100, 1), cagr=round(cagr * 100, 1),
               max_dd=round(mdd * 100, 1), sharpe=round(sharpe, 2), trades=int(len(t)))
    if len(t):
        out.update(win_rate=round(float((t.ret > 0).mean() * 100), 1),
                   avg_trade=round(float(t.ret.mean() * 100), 2),
                   median_trade=round(float(t.ret.median() * 100), 2),
                   avg_hold=round(float(t.held.mean()), 1),
                   exits={k: int(v) for k, v in t.reason.value_counts().items()})
    return out


def index_bh(idx: pd.DataFrame, start, end) -> dict:
    s = idx[(idx.date >= pd.Timestamp(start)) & (idx.date <= pd.Timestamp(end))].set_index("date").close
    days = (s.index[-1] - s.index[0]).days
    dr = s.pct_change().dropna()
    return dict(start=str(s.index[0].date()), end=str(s.index[-1].date()),
                total_return=round((s.iloc[-1] / s.iloc[0] - 1) * 100, 1),
                cagr=round(((s.iloc[-1] / s.iloc[0]) ** (365.25 / days) - 1) * 100, 1),
                max_dd=round(float((1 - s / s.cummax()).max()) * 100, 1),
                sharpe=round(float(dr.mean() / dr.std() * math.sqrt(240)), 2) if len(dr) > 2 else None,
                note="TEDPIX from api.tgju.org (some sessions missing; Sharpe approximate)")


# ------------------------------------------------------------------ variants
def variants() -> dict[str, tuple[S.V2Config, dict]]:
    v1 = S.V2Config(**S.V1_EQUIVALENT)
    rec = S.V2Config()                                  # strategy_v2 defaults = recommended
    return {
        "V1_naive_repo_method": (v1, dict(naive=True)),
        "V1_current_realistic": (v1, {}),
        "V1_fill30pct_when_locked": (v1, dict(fill_locked_prob=0.3)),
        "V2a_open_unlocked": (replace(rec, entry="open_unlocked"), {}),
        "V2b_stop2L_trail10_h10": (replace(rec, sl_limits=2.0, trail=0.10, max_hold=10), {}),
        "V2_recommended": (rec, {}),
    }


def grid(base: S.V2Config):
    for fl in (False, True):
        for mv in (3e8, 2e10, 1e11):
            for rm in (0, 20, 50):
                for sl in ((0.03, None), (None, 2.0), (None, 3.0)):
                    for tr in (None, 0.10):
                        for mh in (5, 10, 15):
                            for mp in (3, 5):
                                yield replace(base, first_lock_only=fl, min_value_rial=mv, regime_ma=rm,
                                              sl_fixed=sl[0], sl_limits=sl[1] or 1.0, trail=tr,
                                              max_hold=mh, max_positions=mp, tp=None if tr else 0.25)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--out", default="../reports/hark_backtest")
    ap.add_argument("--pre_start", default="2022-07-01")
    ap.add_argument("--start", default="2024-09-01")
    ap.add_argument("--split", default="2025-09-30")
    ap.add_argument("--end", default="2026-11-25")
    ap.add_argument("--mc_runs", type=int, default=30)
    ap.add_argument("--grid", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    p = load_panel(a.panel)
    m = Market(p)
    idx = pd.read_csv(a.index, parse_dates=["date"])
    oos_start = str((pd.Timestamp(a.split) + pd.Timedelta(days=1)).date())
    pre_end = str((pd.Timestamp(a.start) - pd.Timedelta(days=1)).date())
    periods = {"full": (a.start, a.end), "in_sample": (a.start, a.split), "out_of_sample": (oos_start, a.end),
               "pre_5pct_limits": (a.pre_start, pre_end)}
    report = dict(data=dict(symbols=int(len(m.syms)), sessions=int(len(m.dates)),
                            first=str(m.dates[0])[:10], last=str(m.dates[-1])[:10],
                            fees=dict(buy=S.BUY_FEE, sell=S.SELL_FEE)),
                  periods=periods, results={}, index={})
    for k, (s, e) in periods.items():
        report["index"][k] = index_bh(idx, s, e)

    curves = {}
    for name, (cfg, kw) in variants().items():
        report["results"][name] = {"config": cfg.to_dict(), **{f"opt_{k}": v for k, v in kw.items()}}
        for k, (s, e) in periods.items():
            res = simulate(m, cfg, s, e, **kw)
            report["results"][name][k] = metrics(res)
            if not kw:
                report["results"][name][k]["random_pick_mc"] = random_pick_mc(m, cfg, s, e, a.mc_runs)
                report["results"][name][k]["event_study"] = event_study(m, cfg, s, e)
                report["results"][name][k]["event_control_random_stocks"] = event_study(m, cfg, s, e, control=40)
            curves.setdefault(k, {})[name] = res["curve"]
            if k == "full":
                pd.DataFrame(res["trades"]).to_csv(out / f"trades_{name}.csv", index=False,
                                                   encoding="utf-8-sig")
        print(name, {k: (v.get("cagr"), v.get("max_dd"), v.get("sharpe"), v.get("trades"))
                     for k, v in report["results"][name].items() if k in periods})

    if a.grid:      # parameter search on IN-SAMPLE only, then score the chosen set OOS
        rows = []
        base = S.V2Config(**S.V1_EQUIVALENT)
        for cfg in grid(base):
            r_is = metrics(simulate(m, cfg, *periods["in_sample"]))
            rows.append(dict(cfg=cfg.to_dict(), **{f"is_{k}": v for k, v in r_is.items()
                                                   if k in ("cagr", "max_dd", "sharpe", "trades")}))
        g = pd.DataFrame(rows).sort_values("is_sharpe", ascending=False)
        top = g.head(10).copy()
        oos = []
        for c in top.cfg:
            r = metrics(simulate(m, S.V2Config(**c), *periods["out_of_sample"]))
            oos.append({f"oos_{k}": r.get(k) for k in ("cagr", "max_dd", "sharpe", "trades")})
        top = pd.concat([top.reset_index(drop=True), pd.DataFrame(oos)], axis=1)
        report["grid"] = dict(n_configs=int(len(g)),
                              is_sharpe_quantiles=g.is_sharpe.quantile([.1, .5, .9]).round(2).tolist(),
                              share_positive_is_cagr=round(float((g.is_cagr > 0).mean()), 3),
                              top10_is_then_oos=json.loads(top.to_json(orient="records")))
        print(top.drop(columns=["cfg"]).to_string())

    (out / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    plot(curves, idx, periods, out / "equity_curves.png")
    return 0


def plot(curves: dict, idx: pd.DataFrame, periods: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(12, 10))
    for ax, key, title in ((axes[0], "full", "3 % price-limit regime (in-sample | out-of-sample)"),
                           (axes[1], "pre_5pct_limits", "Earlier period, wider limits (extra out-of-sample check)")):
        for name, c in curves.get(key, {}).items():
            s = pd.Series([v for _, v in c], index=pd.to_datetime([d for d, _ in c]))
            ls = ":" if "naive" in name else "-"
            lw = 2.4 if name == "V2_recommended" else 1.3
            ax.plot(s.index, s / s.iloc[0], label=name, lw=lw, ls=ls)
        st, en = periods[key]
        ix = idx[(idx.date >= pd.Timestamp(st)) & (idx.date <= pd.Timestamp(en))].set_index("date").close
        ax.plot(ix.index, ix / ix.iloc[0], label="TEDPIX buy & hold", color="black", lw=2, ls="--")
        if key == "full":
            ax.axvline(pd.Timestamp(periods["out_of_sample"][0]), color="grey", ls=":", lw=1)
        ax.set_yscale("log")
        ax.set_title(title)
        ax.set_ylabel("growth of 1 (log)")
        ax.grid(alpha=.3)
        ax.legend(fontsize=8, loc="upper left")
    fig.suptitle("WarRobot ceiling-lock strategy: realistic fills + fees vs the repo's naive method vs TEDPIX")
    fig.tight_layout()
    fig.savefig(path, dpi=120)


if __name__ == "__main__":
    raise SystemExit(main())
