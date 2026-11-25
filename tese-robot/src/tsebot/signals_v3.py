# -*- coding: utf-8 -*-
"""queue_v3 — the durable buy-queue signal.

The whole strategy is: buy a symbol the day before it starts a ceiling-locked
run of >=3 sessions, then hold while it is locked and sell when the queue
breaks. `queue_v3` is the model that ranks candidates for that event.

Two models are bundled:
  full   — daily + queue-state + intraday + Burst features
  daily  — daily + queue-state only (used when intraday/BFE is unavailable)

A pick is marked `degraded=True` when it had to use the daily fallback, so the
execution layer can refuse it (config `signals.require_full_features`).
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from .calendar_ir import now_tehran
from .queue_features import align, day_features, load_bfe, load_intraday
from .signals import load_panel, prepare_panel


@dataclass
class QueuePick:
    symbol: str
    prob: float
    rank: int
    feature_source: str
    degraded: bool
    queue_run_today: int
    dist_to_ceiling_pct: float
    for_date: str = ""
    source: str = "queue_v3"

    @property
    def score(self) -> float:
        return self.prob


_cache: dict = {}


def load_bundle(path: Path | str) -> dict:
    from .config import ROOT
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    key = str(p)
    if key not in _cache:
        with p.open("rb") as fh:
            _cache[key] = pickle.load(fh)
    return _cache[key]


def _history_tables(cfg, day: str | None = None):
    sig = cfg.section("signals")
    intraday = load_intraday(sig.get("intraday_path") or "")
    bfe = load_bfe(sig.get("bfe_path") or "")
    # Prefer the freshly built day file: it is the only source that is guaranteed
    # to contain today's microstructure.
    if day:
        day_file = (cfg.path("picks_dir").parent / "features" / f"feature_day_{day}.csv")
        if day_file.exists():
            fresh = pd.read_csv(day_file, low_memory=False)
            fresh["symbol"] = fresh["symbol"].astype(str).str.strip()
            fresh["date"] = fresh["date"].astype(str)
            if intraday is not None and not intraday.empty:
                intraday = pd.concat([intraday[~intraday["date"].eq(day)], fresh],
                                     ignore_index=True)
            else:
                intraday = fresh
            bfe_cols = [c for c in ("flk", "flc", "fl60", "flk60", "hs30", "hs60",
                                    "pv30", "mhi", "nb1", "nb2", "mxr", "tr", "volM")
                        if c in fresh.columns]
            if bfe_cols:
                fresh_bfe = fresh[["symbol", "date"] + bfe_cols].copy()
                if bfe is not None and not bfe.empty:
                    bfe = pd.concat([bfe[~bfe["date"].eq(day)], fresh_bfe], ignore_index=True)
                else:
                    bfe = fresh_bfe
    return intraday, bfe


def score_day(cfg, day: Optional[str] = None) -> pd.DataFrame:
    """Score every symbol at the close of `day` (default: latest bar)."""
    panel = load_panel(cfg.path("market_data_dir"))
    prepared = prepare_panel(panel)
    if day is None:
        day = max(p["dates"][-1] for p in prepared.values())
    intraday, bfe = _history_tables(cfg, day)
    feats = day_features(prepared, day, float(cfg.get("market.daily_limit_pct", 5.0)),
                         intraday, bfe)
    if feats.empty:
        return feats
    bundle = load_bundle(cfg.get("signals.model_path"))
    out = feats.copy()
    out["prob_full"] = np.nan
    out["prob_daily"] = np.nan

    # The full model is only trustworthy when every one of its inputs exists.
    # `align()` would happily fill the missing Burst columns with NaN.
    has_bfe = out.get("has_bfe", pd.Series(0, index=out.index)).fillna(0).astype(int) == 1
    if has_bfe.any():
        Xf = align(out[has_bfe], bundle["full"]["features"])
        out.loc[Xf.index, "prob_full"] = bundle["full"]["model"].predict_proba(Xf)[:, 1]
    Xd = align(out, bundle["daily"]["features"])
    out["prob_daily"] = bundle["daily"]["model"].predict_proba(Xd)[:, 1]

    use_full = out["prob_full"].notna()
    out["prob"] = np.where(use_full, out["prob_full"], out["prob_daily"])
    out["model_used"] = np.where(use_full, "full", "daily")
    out["degraded"] = ~use_full
    out = out.sort_values("prob", ascending=False).reset_index(drop=True)
    out["rank"] = out.index + 1
    return out


def generate_picks(cfg, day: Optional[str] = None, write: bool = True) -> List[QueuePick]:
    sig = cfg.section("signals")
    top_n = int(sig.get("top_n", 3))
    min_prob = float(sig.get("min_prob", 0.35))
    require_full = bool(sig.get("require_full_features", False))

    scored = score_day(cfg, day)
    if scored.empty:
        return []
    from .signals import _next_g
    day_used = str(scored["date"].iloc[0])
    for_date_g = _next_g(day_used, cfg)
    cand = scored[(scored["prob"] >= min_prob) & (scored["y_locked_today"] == 0)]
    if require_full:
        cand = cand[~cand["degraded"]]
    top = cand.head(top_n)
    picks = [QueuePick(
        symbol=str(r["symbol"]), prob=float(r["prob"]), rank=int(r["rank"]),
        feature_source=str(r["feature_source"]), degraded=bool(r["degraded"]),
        queue_run_today=int(r["y_run_today"]),
        dist_to_ceiling_pct=round(float(r["y_dist_to_ceiling_pct"]), 2),
        for_date=for_date_g,
    ) for _, r in top.iterrows()]

    if write:
        payload = {
            "schema": 3,
            "generated_at": now_tehran().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "queue_v3",
            "mode": "probability",
            "model": "queue_v3",
            "label": "ceiling-locked run of >=3 sessions starting tomorrow",
            "data_day_jal": day_used,
            "for_date_g": for_date_g,
            "min_prob": min_prob,
            "require_full_features": require_full,
            "scored": int(len(scored)),
            "picks": [{"symbol": p.symbol, "score": round(p.prob, 6), "rank": p.rank,
                       "feature_source": p.feature_source, "degraded": p.degraded,
                       "queue_run_today": p.queue_run_today,
                       "dist_to_ceiling_pct": p.dist_to_ceiling_pct} for p in picks],
            "top": [{"symbol": str(r["symbol"]), "score": round(float(r["prob"]), 6),
                     "degraded": bool(r["degraded"])} for _, r in scored.head(15).iterrows()],
            "note": "probability of a >=3-session ceiling-locked run starting tomorrow",
        }
        out_dir = cfg.path("picks_dir")
        out_dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        (out_dir / "picks_v3.json").write_text(text, encoding="utf-8")
        (out_dir / "today_picks.json").write_text(text, encoding="utf-8")
    return picks



