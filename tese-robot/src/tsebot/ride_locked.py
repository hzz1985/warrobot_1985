"""Ride-the-locked-queue signal source (TSE_Robot_V2 result).

A session that closes locked at the ceiling ("قفل") is the entry signal. The model
estimates how likely that lock is to survive at least two more sessions, which is
where the positive expectancy comes from. Entry happens at the next open with a
ceiling price (pre-open auction), exit on the first session that is no longer
locked at the ceiling.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

FEATURES = ["ret1", "ret5", "ret20", "volr5", "volr20", "closepos", "locks10", "days_since"]


@dataclass
class RidePick:
    symbol: str
    score: float
    feature_source: str = "locked_queue"
    degraded: bool = False
    queue_run_today: int = 1
    dist_to_ceiling_pct: float = 0.0
    for_date: str = ""
    source: str = "ride_locked"


def _features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
    g = df.groupby("symbol", group_keys=False)
    df["lock"] = ((df["locked"] == 1) & (df["close_at_ceiling"] == 1)).astype(int)
    df["ret1"] = g["close"].transform(lambda s: s.pct_change()) * 100
    df["ret5"] = g["close"].transform(lambda s: s.pct_change(5)) * 100
    df["ret20"] = g["close"].transform(lambda s: s.pct_change(20)) * 100
    df["volr5"] = g["volume"].transform(lambda s: s / s.rolling(5).mean())
    df["volr20"] = g["volume"].transform(lambda s: s / s.rolling(20).mean())
    df["closepos"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    df["locks10"] = g["lock"].transform(lambda s: s.rolling(10, min_periods=1).sum())
    df["days_since"] = g["lock"].transform(
        lambda s: s.eq(1).cumsum().rsub(s.eq(1).cumsum().where(s.eq(1)).ffill().fillna(0)))
    return df


def train(events_path: str, model_path: str) -> Dict[str, Any]:
    """Fit the durability model once and persist it."""
    df = pd.read_csv(events_path, dtype={"date": str})
    f = _features(df)
    rows = []
    for sym, sub in f.groupby("symbol"):
        lk = sub["lock"].to_numpy(int)
        n = len(sub)
        nxt = np.full(n, n, dtype=int)
        k = n
        for i in range(n - 1, -1, -1):
            if lk[i] == 0:
                k = i
            nxt[i] = k
        for i in range(n - 1):
            if lk[i] != 1:
                continue
            rows.append({
                "y2": int(min(nxt[i + 1], n - 1) - (i + 1) >= 2),
                **{c: sub[c].to_numpy()[i] for c in FEATURES},
            })
    t = pd.DataFrame(rows)
    # a locked session has high == low, so `closepos` is 0/0 by construction; fill
    # instead of dropping (dropping removed every single row and the model never fit)
    t[FEATURES] = t[FEATURES].fillna(0.0)
    from sklearn.ensemble import HistGradientBoostingClassifier
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=4,
                                       random_state=20261020)
    m.fit(t[FEATURES], t["y2"])
    out = {"model": m, "features": FEATURES, "base_rate": float(t["y2"].mean())}
    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    with open(model_path, "wb") as fh:
        pickle.dump(out, fh)
    return {"samples": len(t), "base_rate": out["base_rate"]}


def candidates(events_path: str, model_path: str, min_prob: float = 0.7,
               top_n: int = 3, lookback: int = 3) -> List[RidePick]:
    """Symbols locked at the ceiling in the most recent session(s), scored."""
    with open(model_path, "rb") as fh:
        bundle = pickle.load(fh)
    model = bundle["model"]
    feats = bundle["features"]

    df = pd.read_csv(events_path, dtype={"date": str})
    f = _features(df)
    last_date = f["date"].max()
    tail = f[f["date"] >= f["date"].values[-1]]
    # keep the last `lookback` sessions to survive a market holiday
    dates = sorted(f["date"].unique())[-lookback:]
    tail = f[f["date"].isin(dates)]
    locked = tail[tail["lock"] == 1].copy()
    if locked.empty:
        return []
    locked = locked.drop_duplicates("symbol", keep="last")
    locked["prob"] = model.predict_proba(locked[feats].fillna(0))[:, 1]
    locked = locked[locked["prob"] >= min_prob].sort_values("prob", ascending=False).head(top_n)
    return [RidePick(symbol=r["symbol"], score=float(r["prob"]), for_date=str(r["date"]))
            for _, r in locked.iterrows()]




def next_session_label() -> str:
    """Jalali date of the next trading session (Thu/Fri closed)."""
    import jdatetime
    from datetime import timedelta
    d = jdatetime.date.today()
    for _ in range(10):
        d = d + timedelta(days=1)
        if d.weekday() not in (5, 6):          # 5=Thursday, 6=Friday
            return d.strftime("%Y-%m-%d")
    return ""


def write_candidates(cfg) -> "pd.DataFrame":
    """Nightly scan: rebuild tomorrow's candidates from the freshly downloaded data.

    Runs after the data refresh (~20:30) so it never uses a half-updated session.
    A symbol is a candidate when TODAY closed locked at the ceiling (>=99.9% of the
    computed ceiling with a real close) and the model gives it >= min_prob chance of
    holding the queue for at least three sessions.
    """
    import json
    from pathlib import Path as _P

    daily_dir = _P(str(cfg.path("daily_dir")))
    if not daily_dir.exists():
        daily_dir = _P(__file__).resolve().parents[2] / "data" / "daily"
    events = _P(str(cfg.get("ride.events_path")))
    model_path = _P(str(cfg.get("ride.model_path")))
    min_prob = float(cfg.get("ride.min_prob", 0.5))
    top_n = int(cfg.get("ride.top_n", 3))
    if not model_path.exists() or not events.exists():
        return pd.DataFrame()

    ev = pd.read_csv(events, dtype={"date": str})
    # use the RECENT band only (bands changed over the years) and keep it sane
    recent = ev.sort_values("date").groupby("symbol").tail(120)
    limits = (recent.groupby("symbol")["limit_pct"].agg(lambda s: s.mode().iloc[0]).to_dict())
    limits = {k: float(min(max(v, 1.0), 10.0)) for k, v in limits.items()}
    with open(model_path, "rb") as fh:
        bundle = pickle.load(fh)
    model, feats = bundle["model"], bundle["features"]

    # the market's newest session: a symbol whose file stops earlier is stale and
    # must not be offered as a candidate (ولملت had data only up to 1405-07-21)
    market_last = ""
    for path in daily_dir.glob("*_daily.csv"):
        try:
            _d = pd.read_csv(path, usecols=["date"], dtype={"date": str})
            if len(_d):
                market_last = max(market_last, str(_d["date"].iloc[-1]))
        except Exception:
            continue

    rows = []
    for path in daily_dir.glob("*_daily.csv"):
        sym = path.name[:-len("_daily.csv")]
        try:
            d = pd.read_csv(path, dtype={"date": str})
        except Exception:
            continue
        if len(d) < 25:
            continue
        d = d.sort_values("date").reset_index(drop=True)
        lim = float(limits.get(sym, 5.0))
        c = d["Close"].astype(float)
        prev = c.shift(1)
        ceiling = (prev * (1 + lim / 100)).round(0)
        d["prev_close"] = prev
        d["ceiling"] = ceiling
        d["ret1"] = c.pct_change() * 100
        d["ret5"] = c.pct_change(5) * 100
        d["ret20"] = c.pct_change(20) * 100
        v = d["Volume"].astype(float)
        d["volr5"] = v / v.rolling(5).mean()
        d["volr20"] = v / v.rolling(20).mean()
        d["closepos"] = (c - d["Low"]) / (d["High"] - d["Low"]).replace(0, np.nan)
        d["lock"] = (c >= ceiling * 0.999).astype(int)
        d["locks10"] = d["lock"].rolling(10, min_periods=1).sum()
        d["tests90"] = ((d["High"] >= prev + 0.9 * (ceiling - prev)) & (d["lock"] == 0)).astype(int)
        d["tests90_20"] = d["tests90"].rolling(20, min_periods=1).sum()
        d["high20"] = d["High"].rolling(20, min_periods=5).max()
        d["high60"] = d["High"].rolling(60, min_periods=10).max()
        d["dist_high20"] = (d["high20"] - c) / c * 100
        d["dist_high60"] = (d["high60"] - c) / c * 100
        adv = v.rolling(20, min_periods=5).mean() * c
        d["adv_value_log"] = np.log1p(adv)
        d["range_pct"] = (d["High"] - d["Low"]) / prev.replace(0, np.nan) * 100
        d["days_since"] = d["lock"].eq(1).cumsum().rsub(
            d["lock"].eq(1).cumsum().where(d["lock"].eq(1)).ffill().fillna(0))
        d["run_start"] = ((d["lock"] == 1) & (d["lock"].shift(1).fillna(0) == 0)).astype(int)
        d["prior_runs60"] = d["run_start"].rolling(60, min_periods=1).sum()
        last = d.iloc[-1]
        if market_last and str(last["date"]) != market_last:
            continue                      # stale file: the session did not trade
        if int(last["lock"]) != 1:
            continue
        # sanity: a locked close cannot exceed the ceiling; if it does the band is
        # wrong for this symbol, so the lock cannot be verified from daily data
        if float(last["Close"]) > float(last["ceiling"]) * 1.001:
            continue
        x = {f: (0.0 if pd.isna(last[f]) else float(last[f])) for f in feats}
        prob = float(model.predict_proba(pd.DataFrame([x])[feats])[:, 1][0])
        if prob < min_prob:
            continue
        rows.append({"symbol": sym, "prob": round(prob, 4),
                     "close": float(last["Close"]), "ceiling_today": float(last["ceiling"]),
                     "limit_pct": lim,
                     "next_ceiling_est": round(float(last["Close"]) * (1 + lim / 100), 0),
                     "for_session": next_session_label(), "built_at": pd.Timestamp.now().isoformat(timespec="seconds")})
    out = pd.DataFrame(rows).sort_values("prob", ascending=False).head(top_n) if rows else pd.DataFrame()
    target = _P(str(cfg.path("state_dir"))) / "candidates_next.csv"
    out.to_csv(target, index=False, encoding="utf-8")
    # append every night to a history file so the weekly review can compare
    # the entry distance with what actually happened the next session
    if len(out):
        hist = _P(str(cfg.path("state_dir"))) / "candidates_history.csv"
        new_rows = out.assign(logged_at=pd.Timestamp.now().isoformat(timespec="seconds"))
        try:
            if hist.exists():
                old = pd.read_csv(hist, dtype={"built_at": str, "logged_at": str})
                # one scan per target session: replace its rows instead of appending
                old = old[old["for_session"].astype(str) != str(out["for_session"].iloc[0])]
                new_rows = pd.concat([old, new_rows], ignore_index=True)
        except Exception:
            pass
        new_rows.to_csv(hist, index=False, encoding="utf-8")
    return out



