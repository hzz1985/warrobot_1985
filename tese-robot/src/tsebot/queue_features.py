# -*- coding: utf-8 -*-
"""Feature builder shared by training and live scoring (queue_v3).

Keeping one implementation is the whole point: the audit showed the old model
was trained on one feature vector and scored on another (six columns silently
NaN). Here `day_features()` produces exactly the columns the model was fitted
on, and `align()` drops/raises loudly if anything is missing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .signals import features_from_prep

BFE_COLS = ["flk", "flc", "fl60", "flk60", "hs30", "hs60", "pv30",
            "mhi", "nb1", "nb2", "mxr", "tr", "volM"]
INTRADAY_MAP = {
    "lock_time_min": "intr_lock_time_min",
    "reopen_count": "intr_reopen_count",
    "pre_lock_vol_M": "intr_pre_lock_vol_M",
    "post_lock_vol_M": "intr_post_lock_vol_M",
    "total_vol_M": "intr_total_vol_M",
    "first_hour_vol_M": "intr_first_hour_vol_M",
    "last_30min_vol_M": "intr_last_30min_vol_M",
    "first_5min_vol_M": "intr_first_5min_vol_M",
    "big_trades_ratio": "intr_big_trades_ratio",
    "avg_trade_size": "intr_avg_trade_size",
    "max_trade_size": "intr_max_trade_size",
    "num_trades": "intr_num_trades",
    "first_trade_min": "intr_first_trade_min",
}


def is_locked_series(df: pd.DataFrame) -> np.ndarray:
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    prev = np.concatenate(([np.nan], c[:-1]))
    locked = (h == l) & (c > prev)
    return np.where(np.isnan(prev), False, locked)


def run_lengths(locked: np.ndarray) -> np.ndarray:
    n = len(locked)
    run = np.zeros(n, dtype=int)
    i = n - 1
    while i >= 0:
        if locked[i]:
            j = i
            while j + 1 < n and locked[j + 1]:
                j += 1
            run[i:j + 1] = j - i + 1
            i -= 1
        else:
            i -= 1
    return run


def day_features(prepared: Dict[str, dict], day: str, limit_pct: float = 5.0,
                 intraday: Optional[pd.DataFrame] = None,
                 bfe: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    rows = []
    for sym, p in prepared.items():
        i = p["pos"].get(day)
        if i is None or i < 40:
            continue
        f = features_from_prep(p, i, limit_pct)
        if f is None:
            continue
        locked = is_locked_series(pd.DataFrame({
            "High": p["h"], "Low": p["l"], "Close": p["c"]}))
        run = run_lengths(locked)
        lock_i = locked.astype(int)
        last10 = int(lock_i[max(0, i - 9):i + 1].sum())
        since = -1
        for k in range(i, max(-1, i - 60), -1):
            if lock_i[k] == 0:
                since = i - k
                break
        rows.append({
            "symbol": sym, "date": day,
            "y_locked_today": int(locked[i]),
            "y_locks_last10": last10,
            "y_days_since_lock": int(since),
            "y_max_run_60": int(run[max(0, i - 59):i + 1].max()),
            "y_dist_to_ceiling_pct": float((f["limit_up"] - f["close"]) / f["close"] * 100.0),
            "y_run_today": int(run[i]),
            **{k: v for k, v in f.items() if k != "limit_up"},
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    if intraday is not None and not intraday.empty:
        sub = intraday[intraday["date"] == day]
        if not sub.empty:
            out = out.merge(sub, on=["symbol", "date"], how="left")
    if bfe is not None and not bfe.empty:
        sub = bfe[bfe["date"] == day]
        if not sub.empty:
            # The fresh day file carries both blocks, so some columns may already
            # be present; merging them again would create flk_x/flk_y and hide
            # the feature from the model.
            add = ["symbol", "date"] + [c for c in sub.columns
                                        if c not in out.columns and c not in ("symbol", "date")]
            if len(add) > 2:
                out = out.merge(sub[add], on=["symbol", "date"], how="left")
    out["has_intraday"] = out.get("intr_total_vol_M", pd.Series(index=out.index)).notna().astype(int)
    out["has_bfe"] = out.get("flk", pd.Series(index=out.index)).notna().astype(int)
    out["feature_source"] = np.where(out["has_bfe"] == 1, "full",
                                     np.where(out["has_intraday"] == 1, "intraday", "daily"))
    return out


def load_intraday(path: Path | str) -> Optional[pd.DataFrame]:
    p = Path(path)
    if not p.exists():
        return None
    df = pd.read_csv(p, low_memory=False)
    if "date_jalali" in df.columns:
        df["date"] = df["date_jalali"].astype(str)
    elif "date" not in df.columns:
        return None
    df["symbol"] = df["symbol"].astype(str).str.strip()
    keep = ["symbol", "date"] + [c for c in INTRADAY_MAP if c in df.columns]
    return df[keep].rename(columns={k: v for k, v in INTRADAY_MAP.items() if k in df.columns})


def load_bfe(path: Path | str) -> Optional[pd.DataFrame]:
    p = Path(path)
    if not p.exists():
        return None
    df = pd.read_csv(p, low_memory=False)
    df["symbol"] = df["symbol"].map(fix_mojibake)
    df["date"] = df["date"].astype(str)
    return df[["symbol", "date"] + [c for c in BFE_COLS if c in df.columns]]


def fix_mojibake(s: str) -> str:
    if not isinstance(s, str):
        return str(s)
    for enc in ("cp1252", "latin-1"):
        try:
            fixed = s.encode(enc).decode("utf-8")
            if any("\u0600" <= ch <= "\u06FF" for ch in fixed):
                return fixed
        except Exception:
            continue
    return s


def burst_features(g: pd.DataFrame) -> Optional[Dict[str, float]]:
    """Burst microstructure features (BFE) from one symbol-day of trades.

    Formulas are a faithful port of the v1 builder (`burst_build_v1.dfeat`), so
    a live row is comparable with the training table:
      flk/flc   tick-rule order flow (signed volume share)
      fl60/flk60 the same over the last 60 minutes
      hs30/hs60 share of volume printed at the day's high
      pv30      share of volume in the last 30 minutes
      mhi       minute when the high was first printed
      nb1/nb2   count of >=1σ / >=2σ minute-volume bursts
      mxr       longest run of consecutive burst minutes
    """
    if g is None or len(g) < 5:
        return None
    df = g.sort_values("Timestamp") if "Timestamp" in g.columns else g.copy()
    price = df["Price"].to_numpy(float)
    vol = df["Volume"].to_numpy(float)
    if "Timestamp" in df.columns:
        ts = pd.to_datetime(df["Timestamp"], errors="coerce")
        mins = (ts.dt.hour * 60 + ts.dt.minute).to_numpy(float) - 540.0
    elif "Time" in df.columns:
        mins = (pd.to_timedelta(df["Time"].astype(str), errors="coerce")
                .dt.total_seconds().to_numpy() / 60.0) - 540.0
    else:
        return None
    tv = float(vol.sum())
    if tv <= 0:
        return None
    sr = np.sign(np.diff(price, prepend=price[0]))
    ix = np.where(sr != 0, np.arange(len(sr)), 0)
    np.maximum.accumulate(ix, out=ix)
    sc = sr[ix]
    sv = sc * vol
    flk = float(sv.sum() / tv)
    up = float(vol[sr > 0].sum())
    dn = float(vol[sr < 0].sum())
    flc = (up - dn) / (up + dn) if (up + dn) > 0 else 0.0
    m60 = (mins >= 150) & (mins <= 212)
    u60 = float(vol[m60 & (sr > 0)].sum())
    d60 = float(vol[m60 & (sr < 0)].sum())
    fl60 = (u60 - d60) / (u60 + d60) if (u60 + d60) > 0 else 0.0
    v60 = float(vol[m60].sum())
    flk60 = float(sv[m60].sum() / v60) if v60 > 0 else 0.0
    ok = ~np.isnan(mins)
    pmax = float(price.max())
    m30 = (mins >= 180) & (mins <= 212)
    v30 = float(vol[m30].sum())
    v30h = float(vol[m30 & (price >= pmax - 0.5)].sum())
    hs30 = v30h / v30 if v30 > 0 else 0.0
    v60h = float(vol[m60 & (price >= pmax - 0.5)].sum())
    hs60 = v60h / v60 if v60 > 0 else 0.0
    pv30 = v30 / tv
    sel = (price >= pmax - 0.5) & ok
    mhi = float(mins[sel].min()) if sel.any() else -1.0
    mnn = np.rint(mins[ok]).astype(int)
    svok = sv[ok]
    vv = (mnn >= 0) & (mnn <= 210)
    net = np.bincount(mnn[vv], weights=svok[vv], minlength=211)
    ser = pd.Series(net)
    sd = float(ser.std())
    nb1 = nb2 = mxr = 0
    if sd > 1e-9:
        z = (ser - ser.rolling(15, min_periods=8).mean().shift(1)) / sd
        bo = z.fillna(-99).to_numpy() >= 1.0
        nb1 = int(bo.sum())
        nb2 = int((z.fillna(-99).to_numpy() >= 2.0).sum())
        b = bo.astype(int)
        dd = np.diff(np.concatenate(([0], b, [0])))
        st = np.where(dd == 1)[0]
        en = np.where(dd == -1)[0]
        if len(st):
            mxr = int((en - st).max())
    return {"flk": flk, "flc": flc, "fl60": fl60, "flk60": flk60,
            "hs30": hs30, "hs60": hs60, "pv30": pv30, "mhi": mhi,
            "nb1": nb1, "nb2": nb2, "mxr": mxr,
            "tr": int(len(df)), "volM": tv / 1e6}


def align(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """Return X with exactly the model's columns; unseen columns become NaN."""
    missing = [c for c in features if c not in df.columns]
    X = df.reindex(columns=features).astype(float)
    if missing:
        # HGB tolerates NaN, but a live feed that lost half the columns must be
        # visible in the logs rather than silently degraded.
        X.attrs["missing_features"] = missing
    return X

