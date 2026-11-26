"""Market matrices + point-in-time fundamentals for the fundamentals-strategy backtest.

Inputs (built in the sandbox, see STRATEGY_FUND_HARK.md):
  panel_full.pkl   TSETMC daily history 2017-03..2026-11 (GetClosingPriceDailyList, all instrument codes)
  fund/eps/<sym>.json        rahavard365 EPS history (publish dates)
  ratios/<sym>.json          rahavard365 annual ROE (publish dates)
  bundle json                Codal ن-10 extractions (server crawl cache)
  clienttype/<ins>.json      TSETMC individual / institutional daily flows (2022-06+)
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))           # warrobot/
import fund_data as FD                             # noqa: E402

TSE = os.environ.get("TSEDATA", "/workspace/work/tsedata")
RATIOS = os.environ.get("RATIOS", "/workspace/work/fundres/ratios")
BUNDLE = os.environ.get("CODAL_BUNDLE", "/workspace/work/fundres/bundle_20261126.json")
LIMIT_LEVELS = (0.07, 0.06, 0.05, 0.04, 0.03, 0.02)


def load_panel(path=None) -> pd.DataFrame:
    p = pd.read_pickle(path or f"{TSE}/panel_full.pkl")
    p = p[p.board.isin([1, 2]) & ~p.sym.str.contains(r"\d$", regex=True)]
    p = p[(p.ntr > 0) & (p.py > 0) & (p.h > 0) & (p.c > 0)].copy()
    p["ru"] = p.h / p.py - 1

    def lim(s):
        for k in LIMIT_LEVELS:
            if ((s - k).abs() < 0.0015).sum() >= 3:
                return k
        return np.nan

    L = p.groupby(["date", "board"]).ru.apply(lim).rename("L").reset_index()
    p = p.merge(L, on=["date", "board"], how="left")
    p["L"] = p["L"].fillna(0.05)
    within = p.h <= p.py * (1 + p.L) * 1.003
    # locked at the ceiling all session: low == high at the ceiling (nothing traded below it)
    p["lock_up"] = within & (p.l >= p.py * (1 + p.L) * 0.997)
    p["lock_dn"] = p.h <= p.py * (1 - p.L) * 1.003
    return p.sort_values(["sym", "date"]).reset_index(drop=True)


class Market:
    def __init__(self, p: pd.DataFrame):
        self.dates = np.array(sorted(p.date.unique()))
        self.syms = np.array(sorted(p.sym.unique()))
        di = {d: i for i, d in enumerate(self.dates)}
        si = {s: j for j, s in enumerate(self.syms)}
        I, J = p.date.map(di).to_numpy(), p.sym.map(si).to_numpy()
        shape = (len(self.dates), len(self.syms))

        def mat(col, fill=np.nan, dtype=float):
            m = np.full(shape, fill, dtype=dtype)
            m[I, J] = p[col].to_numpy()
            return m

        self.py, self.c, self.val = mat("py"), mat("c"), mat("val", 0.0)
        self.up, self.dn = mat("lock_up", False, bool), mat("lock_dn", False, bool)
        self.traded = ~np.isnan(self.c)
        r = np.where(self.traded, self.c / self.py - 1, 0.0)
        # guard: broken reference prices (relisting placeholders) -> use the previous traded close
        prevc = pd.DataFrame(self.c).ffill().shift(1).to_numpy()
        bad = self.traded & ((r > 0.6) | (r < -0.6))
        alt = np.where(bad & ~np.isnan(prevc), self.c / prevc - 1, r)
        r = np.where(bad & ((alt > 0.6) | (alt < -0.6)), 0.0, alt)
        self.r = np.nan_to_num(r)
        self.tr = np.cumprod(1 + self.r, axis=0)
        lastc = pd.DataFrame(self.c).ffill().to_numpy()
        self.lastc = lastc
        tf = pd.DataFrame(self.traded.astype(float))
        self.traded60 = tf.rolling(60, min_periods=60).mean().to_numpy()
        self.val60 = pd.DataFrame(self.val).rolling(60, min_periods=60).mean().to_numpy()
        ew = np.where(self.traded & (np.nan_to_num(self.val60) >= 20e9), np.clip(self.r, -0.25, 0.25), np.nan)
        cnt = np.sum(~np.isnan(ew), axis=1)
        self.ew_ret = np.nan_to_num(np.nanmean(ew, axis=1))
        self.ew = np.cumprod(1 + self.ew_ret)
        self.ew_count = cnt
        self.si = si
        self.ins = p.groupby("sym").ins.last().to_dict()

    def mom(self, i, lb=126, skip=5):
        if i - lb < 0:
            return np.full(len(self.syms), np.nan)
        return self.tr[i - skip] / self.tr[i - lb] - 1

    def regime_ok(self, ma):
        s = pd.Series(self.ew).rolling(ma, min_periods=ma).mean().to_numpy()
        return np.nan_to_num(self.ew > s, nan=0).astype(bool)


def load_flow(m: Market) -> np.ndarray:
    """20-session net individual inflow / traded value (NaN where unknown)."""
    T, N = len(m.dates), len(m.syms)
    net = np.full((T, N), np.nan)
    di = {pd.Timestamp(d).strftime("%Y%m%d"): i for i, d in enumerate(m.dates)}
    for s, j in m.si.items():
        fp = f"{TSE}/clienttype/{m.ins.get(s)}.json"
        if not os.path.exists(fp):
            continue
        try:
            rows = json.load(open(fp))
        except Exception:
            continue
        for r in rows:
            i = di.get(str(r[0]))
            if i is not None and r[3] is not None and r[9] is not None:
                net[i, j] = float(r[3]) - float(r[9])
    num = pd.DataFrame(np.nan_to_num(net)).rolling(20, min_periods=20).sum().to_numpy()
    den = pd.DataFrame(m.val).rolling(20, min_periods=20).sum().to_numpy()
    have = pd.DataFrame((~np.isnan(net)).astype(float)).rolling(20, min_periods=20).sum().to_numpy()
    out = np.where((den > 0) & (have >= 15), num / np.where(den > 0, den, 1), np.nan)
    return out


class Fundamentals:
    """per-symbol event lists; snapshot(sym, asof) -> fund_data.snapshot dict."""

    def __init__(self, syms, use_codal=True):
        self.eps, self.roe, self.codal = {}, {}, {}
        epsdir = f"{TSE}/fund/eps"
        files = {FD.nsym(f[:-5]): f for f in os.listdir(epsdir) if f.endswith(".json")}
        rfiles = {FD.nsym(f[:-5]): f for f in os.listdir(RATIOS) if f.endswith(".json")} if os.path.isdir(RATIOS) else {}
        cod = {}
        if use_codal and os.path.exists(BUNDLE):
            b = json.load(open(BUNDLE, encoding="utf-8"))
            for ex in b["ext"].values():
                if ex.get("lt") == 6 and ex.get("sym") and ex.get("pub_g") and ex.get("n_tables"):
                    cod.setdefault(FD.nsym(ex["sym"]), []).append(ex)
        self.cov = dict(eps=0, roe=0, codal=0)
        for s in syms:
            k = FD.nsym(s)
            if k in files:
                try:
                    d = json.load(open(f"{epsdir}/{files[k]}", encoding="utf-8"))
                    if d.get("type") in (None, "سهام"):
                        ev = FD.eps_events(d)
                        if ev:
                            self.eps[s] = ev; self.cov["eps"] += 1
                except Exception:
                    pass
            if k in rfiles:
                try:
                    ev = FD.ratio_events(json.load(open(f"{RATIOS}/{rfiles[k]}")))
                    if ev:
                        self.roe[s] = ev; self.cov["roe"] += 1
                except Exception:
                    pass
            if k in cod:
                ev = FD.codal_events(cod[k])
                if ev:
                    self.codal[s] = ev; self.cov["codal"] += 1

    def snapshot(self, s, asof):
        if s not in self.eps and s not in self.codal:
            return None
        return FD.snapshot(self.eps.get(s, []), self.roe.get(s, []), self.codal.get(s), asof)

    def report_days(self, s):
        return sorted({e["pub"] for e in self.eps.get(s, [])} | {e["pub"] for e in self.codal.get(s, [])})
