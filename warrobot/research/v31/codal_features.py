"""Point-in-time Codal feature matrices (date x symbol) for the V3.1 study.

A report is usable on signal day d when its Codal publish date <= d (decision is taken for the next
session's pre-open).  If the newest report cannot be parsed, the previous parsed one stays in force.
Equity is carried forward from the latest report that had a balance sheet.
"""
from __future__ import annotations
import json, math, os, re, sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/workspace/work/codal3/repo/warrobot")
import codal_fund as C  # noqa: E402

CAP = "/workspace/work/codal3/cap"


def _d(s):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s or "")
    return m.group(1) if m else None


def cap_history(sym):
    fp = f"{CAP}/{sym}.json"
    if not os.path.exists(fp):
        return [], []
    d = json.load(open(fp, encoding="utf-8")).get("data") or {}
    ch = d.get("capital_changes") or []
    caps, raises = [], []
    for x in ch:
        reg = _d(x.get("registration_date")) or _d(x.get("date"))
        if not reg or not x.get("new_capital"):
            continue
        if x.get("is_capital_changed") is False and not x.get("capital_change"):
            continue
        caps.append((reg, float(x["new_capital"]), float(x.get("previous_capital") or 0)))
        if x.get("capital_change", 0) > 0 and x.get("is_reserve") and not x.get("is_contribution"):
            raises.append(reg)
    caps.sort()
    return caps, sorted(raises)


def build(m, bundle_path="/workspace/work/codal3/bundle.json"):
    b = json.load(open(bundle_path, encoding="utf-8"))
    by = {}
    for tn, ex in b["ext"].items():
        if ex.get("sym") and ex.get("pub_g"):
            by.setdefault((ex["sym"], ex.get("lt")), []).append(ex)
    dates = pd.to_datetime(m.dates).strftime("%Y-%m-%d").to_numpy()
    T, N = len(dates), len(m.syms)
    out = {k: np.full((T, N), np.nan) for k in ("pe_c", "roe", "npm", "np_yoy", "sales_yoy", "capr_days", "np_ttm", "fund_age_c")}
    cov = dict(stmt_symbols=0, monthly_symbols=0, cap_symbols=0, stmt_reports=0, stmt_parsed=0, monthly_reports=0, monthly_parsed=0)
    for j, s in enumerate(m.syms):
        s = str(s)
        base = re.sub(r"\d+$", "", s).strip()
        # ---------- statements
        evs = []
        for ex in by.get((base, 6), []):
            cov["stmt_reports"] += 1
            st = C.parse_statement(ex)
            if not st:
                continue
            mt = C.statement_metrics(st)
            if math.isnan(mt["np_ttm"]):
                continue
            cov["stmt_parsed"] += 1
            evs.append((ex["pub_g"], mt))
        evs.sort(key=lambda z: z[0])
        caps, raises = cap_history(base)
        if caps:
            cov["cap_symbols"] += 1
        if evs:
            cov["stmt_symbols"] += 1
            eq_last = np.nan
            k = 0; cur = None
            ci = 0; capital_now = caps[0][2] if caps and caps[0][2] else np.nan
            for i, d in enumerate(dates):
                while k < len(evs) and evs[k][0] <= d:
                    mt = dict(evs[k][1])
                    if not math.isnan(mt["equity"]):
                        eq_last = mt["equity"]
                    elif not math.isnan(eq_last):
                        mt["equity"] = eq_last
                        mt["roe"] = mt["np_ttm"] / eq_last if eq_last > 0 else np.nan
                    cur = mt; k += 1
                while ci < len(caps) and caps[ci][0] <= d:
                    capital_now = caps[ci][1]; ci += 1
                if cur is None:
                    continue
                shares = capital_now / 1000.0 if not math.isnan(capital_now) else (cur["capital"] * 1000.0 if not math.isnan(cur["capital"]) else np.nan)
                px = m.c[i, j]
                if not math.isnan(px) and not math.isnan(shares) and shares > 0:
                    npr = cur["np_ttm"] * 1e6
                    out["pe_c"][i, j] = px * shares / npr if npr > 0 else -1.0
                out["roe"][i, j] = cur["roe"]
                out["npm"][i, j] = cur["npm"]
                out["np_yoy"][i, j] = cur["np_yoy"]
                out["np_ttm"][i, j] = cur["np_ttm"]
                out["fund_age_c"][i, j] = (pd.Timestamp(d) - pd.Timestamp(cur["pub"])).days if cur.get("pub") else np.nan
        # ---------- monthly
        mev = []
        for ex in by.get((base, 58), []):
            cov["monthly_reports"] += 1
            mo = C.parse_monthly(ex)
            if mo and not math.isnan(mo["sales_yoy"]):
                cov["monthly_parsed"] += 1
                mev.append((ex["pub_g"], mo["sales_yoy"]))
        mev.sort()
        if mev:
            cov["monthly_symbols"] += 1
            k = 0; cur = np.nan; cpub = None
            for i, d in enumerate(dates):
                while k < len(mev) and mev[k][0] <= d:
                    cpub, cur = mev[k]; k += 1
                if cpub and (pd.Timestamp(d) - pd.Timestamp(cpub)).days <= 75:
                    out["sales_yoy"][i, j] = cur
        # ---------- non-cash capital raise
        if raises:
            k = 0; last = None
            for i, d in enumerate(dates):
                while k < len(raises) and raises[k] <= d:
                    last = raises[k]; k += 1
                if last:
                    out["capr_days"][i, j] = (pd.Timestamp(d) - pd.Timestamp(last)).days
    # ---------- rahavard annual ROE (PIT by publish date) for the hybrid fallback
    roe_r = np.full((T, N), np.nan); cov["ratios_symbols"] = 0
    for j, s in enumerate(m.syms):
        base = re.sub(r"\d+$", "", str(s)).strip()
        fp = f"/workspace/work/codal3/ratios/{base}.json"
        if not os.path.exists(fp):
            continue
        ev = sorted((_d(r["date"]), r["roe"]) for r in json.load(open(fp)) if _d(r.get("date")) and r.get("roe") is not None)
        if not ev:
            continue
        cov["ratios_symbols"] += 1
        k = 0; cur = np.nan
        for i, d in enumerate(dates):
            while k < len(ev) and ev[k][0] <= d:
                cur = float(ev[k][1]) / 100.0; k += 1
            roe_r[i, j] = cur
    out["roe_rahavard"] = roe_r
    out["coverage"] = cov
    return out


def hybrid(F):
    """best available PIT source per cell: Codal first, rahavard365 fallback."""
    H = {}
    H["pe_c"] = np.where(~np.isnan(F["pe_c"]), F["pe_c"], F["pe"])
    H["roe"] = np.where(~np.isnan(F["roe"]), F["roe"], F["roe_rahavard"])
    H["np_yoy"] = np.where(~np.isnan(F["np_yoy"]), F["np_yoy"], F["growth"])
    return H
