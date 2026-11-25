"""Point-in-time feature matrices for strategy V3 (date x symbol), aligned with backtest_v2.Market.

fundamentals (rahavard365 EPS history = Codal statements, publish day):
    known from the session AFTER the publish day (no look-ahead)
    ttm_eps  : trailing-12-month EPS on the latest share count (TTM when last-year figures are known,
               otherwise the current cumulative EPS annualised -> flag fund_ann=1)
    pe       : close / ttm_eps  (NaN if unknown, <=0 -> loss-making)
    growth   : cumulative net profit vs the same period last year ((cur-prev)/|prev|), NaN if unknown
money flow (tsetmc ClientType, حقیقی/حقوقی):
    bp       : individual buyer power = (buy_I_Value/buy_I_Count) / (sell_I_Value/sell_I_Count)
    inflow   : net individual money (buy_I_Value - sell_I_Value) / traded value  (signal day)
    inflow5  : same over the last 5 traded sessions
sector:
    sec_mom  : equal-weight 20-session return of the symbol's sector (tsetmc sector code), others only
"""
from __future__ import annotations
import json, os, re, glob
import numpy as np
import pandas as pd

TSE = os.environ.get("TSEDATA", "/workspace/work/tsedata")


def _d(s):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s or "")
    return pd.Timestamp(m.group(1)) if m else None


def base_sym(s):
    return re.sub(r"\d+$", "", s).strip()


def load_eps(sym):
    fp = f"{TSE}/fund/eps/{base_sym(sym)}.json"
    if not os.path.exists(fp):
        fp = f"{TSE}/fund/eps/{sym}.json"
        if not os.path.exists(fp):
            return None
    d = json.load(open(fp, encoding="utf-8"))
    if not d.get("eps"):
        return []
    out = []
    for fy in d["eps"].get("eps_fiscal_years") or []:
        for e in fy.get("epses") or []:
            k = int(e.get("fiscal_period") or 0) or 12
            pub, fye = _d(e.get("date_time")), _d(e.get("fiscal_year"))
            if pub is None or fye is None or not e.get("capital"):
                continue
            out.append(dict(pub=pub, fy=fye, k=k, eps=float(e.get("pure_realized_eps") or 0.0),
                            cap=float(e["capital"])))
    return out


def fundamentals(m, syms):
    """returns dict of matrices pe, ttm_eps, growth, fund_ann, fund_known (dates x syms)"""
    T, N = len(m.dates), len(syms)
    ttm = np.full((T, N), np.nan); gr = np.full((T, N), np.nan); ann = np.zeros((T, N), bool)
    age = np.full((T, N), np.nan)
    dates = pd.to_datetime(m.dates)
    cov = 0
    for j, s in enumerate(syms):
        rep = load_eps(str(s))
        if not rep:
            continue
        cov += 1
        rep.sort(key=lambda r: r["pub"])
        fys = sorted({r["fy"] for r in rep})
        prev_fy = {fy: next((f for f in reversed(fys) if (fy - f).days > 300), None) for fy in fys}
        # event dates: state changes the session after each publish day
        known = {}
        ev = [(r["pub"] + pd.Timedelta(days=1), r) for r in rep]
        ei = 0
        cur = (np.nan, np.nan, False, None)
        for i, dt in enumerate(dates):
            changed = False
            while ei < len(ev) and ev[ei][0] <= dt:
                r = ev[ei][1]; known[(r["fy"], r["k"])] = r; ei += 1; changed = True
            if changed and known:
                fy, k = max(known)               # latest fiscal year, longest period
                r = known[(fy, k)]
                npc = r["eps"] * r["cap"] / 1000.0
                sh = r["cap"] / 1000.0
                pf = prev_fy.get(fy)
                g = np.nan; a = False
                if k == 12:
                    t_np = npc
                elif pf is not None and (pf, 12) in known and (pf, k) in known:
                    p12, pk = known[(pf, 12)], known[(pf, k)]
                    t_np = npc + p12["eps"] * p12["cap"] / 1000 - pk["eps"] * pk["cap"] / 1000
                else:
                    t_np = npc * 12.0 / k; a = True
                if pf is not None and (pf, k) in known:
                    pk = known[(pf, k)]; pv = pk["eps"] * pk["cap"] / 1000
                    if pv != 0:
                        g = (npc - pv) / abs(pv)
                cur = (t_np / sh if sh else np.nan, g, a, r["pub"])
            if cur[3] is not None:
                ttm[i, j], gr[i, j], ann[i, j] = cur[0], cur[1], cur[2]
                age[i, j] = (dt - cur[3]).days
    with np.errstate(divide="ignore", invalid="ignore"):
        pe = np.where(ttm > 0, m.c / ttm, np.where(np.isnan(ttm), np.nan, -1.0))
    return dict(ttm_eps=ttm, pe=pe, growth=gr, fund_ann=ann, fund_age=age,
                fund_known=~np.isnan(ttm), coverage_fund_symbols=cov)


def moneyflow(m, ins_of_sym):
    T, N = len(m.dates), len(m.syms)
    bp = np.full((T, N), np.nan); inflow = np.full((T, N), np.nan); inflow5 = np.full((T, N), np.nan)
    di = {pd.Timestamp(d).strftime("%Y%m%d"): i for i, d in enumerate(m.dates)}
    cov = 0
    for j, s in enumerate(m.syms):
        ins = ins_of_sym.get(str(s))
        fp = f"{TSE}/clienttype/{ins}.json" if ins else None
        if not fp or not os.path.exists(fp):
            continue
        rows = json.load(open(fp))
        if not rows:
            continue
        cov += 1
        a = np.array([[r[0], r[3] or 0, r[9] or 0, r[5] or 0, r[11] or 0] for r in rows], float)
        a = a[np.argsort(a[:, 0])]
        idx = [di.get(str(int(x))) for x in a[:, 0]]
        ok = np.array([k is not None for k in idx])
        a = a[ok]; I = np.array([k for k in idx if k is not None], int)
        if not len(I):
            continue
        bI, sI, bC, sC = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        with np.errstate(divide="ignore", invalid="ignore"):
            p = np.where((bC > 0) & (sC > 0) & (sI > 0), (bI / bC) / (sI / sC), np.nan)
        net = bI - sI
        val = np.nan_to_num(m.val[I, j])
        with np.errstate(divide="ignore", invalid="ignore"):
            inflow[I, j] = np.where(val > 0, net / val, np.nan)
            n5 = pd.Series(net).rolling(5, min_periods=3).sum().to_numpy()
            v5 = pd.Series(val).rolling(5, min_periods=3).sum().to_numpy()
            inflow5[I, j] = np.where(v5 > 0, n5 / v5, np.nan)
        bp[I, j] = p
    return dict(bp=bp, inflow=inflow, inflow5=inflow5, coverage_flow_symbols=cov)


def sectors(m, ins_of_sym):
    sec = {}
    for s in m.syms:
        ins = ins_of_sym.get(str(s))
        fp = f"{TSE}/fund/instinfo/{ins}.json"
        if ins and os.path.exists(fp):
            try:
                sec[str(s)] = (json.load(open(fp, encoding="utf-8")).get("sector") or {}).get("cSecVal", "").strip() or None
            except Exception:
                pass
    codes = np.array([sec.get(str(s)) or "" for s in m.syms])
    T, N = len(m.dates), len(m.syms)
    r = np.where(m.traded, np.clip(m.c / m.py - 1, -0.25, 0.25), np.nan)
    mom = np.full((T, N), np.nan)
    for code in set(codes) - {""}:
        cols = np.where(codes == code)[0]
        if len(cols) < 3:
            continue
        sub = r[:, cols]
        s_sum = pd.DataFrame(np.nan_to_num(sub)).rolling(20, min_periods=10).sum().to_numpy()
        s_cnt = pd.DataFrame((~np.isnan(sub)).astype(float)).rolling(20, min_periods=10).sum().to_numpy()
        tot_sum, tot_cnt = s_sum.sum(1, keepdims=True), s_cnt.sum(1, keepdims=True)
        with np.errstate(divide="ignore", invalid="ignore"):
            # average daily return of the OTHER stocks in the sector over 20 sessions, x20
            mom[:, cols] = (tot_sum - s_sum) / (tot_cnt - s_cnt) * 20
    return dict(sec_mom=mom, sector=codes, coverage_sector_symbols=int((codes != "").sum()))


def build(m, panel):
    ins_of_sym = panel.groupby("sym").ins.last().astype(str).to_dict()
    f = fundamentals(m, m.syms)
    mf = moneyflow(m, ins_of_sym)
    sc = sectors(m, ins_of_sym)
    return {**f, **mf, **sc}
