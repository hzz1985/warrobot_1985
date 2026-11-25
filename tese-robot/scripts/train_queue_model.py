# -*- coding: utf-8 -*-
"""Train and validate the durable buy-queue model (queue_v3).

Two models are produced:
  full   — daily + queue-state + intraday + Burst features (best, needs BFE)
  daily  — daily + queue-state only (fallback when BFE is unavailable)

Validation is strictly walk-forward in time: the last N dates are held out and
never seen during fitting. Reported metrics are the ones that matter for this
strategy: top-1/3/5 precision per day, lift over the base rate, and recall.
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tsebot import config as config_mod  # noqa: E402

LABELS = {"y3", "y5", "y7", "q_days"}
META = {"symbol", "date", "feature_source", "has_intraday", "has_bfe"}
EXCLUDE_ALWAYS = {"close", "prev_close", "limit_up"}


def feature_cols(df: pd.DataFrame, allow_intraday: bool) -> list[str]:
    cols = []
    for c in df.columns:
        if c in LABELS or c in META or c in EXCLUDE_ALWAYS:
            continue
        if not allow_intraday and (c.startswith("intr_") or c in
                                   {"flk", "flc", "fl60", "flk60", "hs30", "hs60",
                                    "pv30", "mhi", "nb1", "nb2", "mxr", "tr", "volM"}):
            continue
        if df[c].dtype == object:
            continue
        cols.append(c)
    return cols


def precision_at_k(test: pd.DataFrame, col: str, k: int, label: str) -> dict:
    precs, counts, hits = [], 0, 0
    for _, g in test.groupby("date"):
        top = g.nlargest(k, col)
        counts += len(top)
        hits += int(top[label].sum())
        if len(top):
            precs.append(float(top[label].mean()))
    base = float(test[label].mean())
    return {
        "k": k,
        "days": len(precs),
        "picks": counts,
        "hits": hits,
        "precision_dayavg": round(float(np.mean(precs)) * 100, 2) if precs else 0.0,
        "precision_pooled": round(hits * 100.0 / max(counts, 1), 2),
        "base_rate": round(base * 100, 2),
        "lift": round((hits / max(counts, 1)) / base, 2) if base > 0 else 0.0,
        "recall": round(hits * 100.0 / max(int(test[label].sum()), 1), 2),
    }


def train_one(df: pd.DataFrame, feats: list[str], cut: str, label: str = "y3") -> dict:
    tr = df[df["date"] < cut]
    te = df[df["date"] >= cut]
    tr = tr[tr[label].notna()]
    te = te[te[label].notna()]
    X = tr[feats].to_numpy(float)
    yv = tr[label].to_numpy(int)
    m = HistGradientBoostingClassifier(
        max_iter=400, learning_rate=0.05, max_depth=6, min_samples_leaf=30,
        l2_regularization=1.0, class_weight="balanced", random_state=42,
        early_stopping=True, validation_fraction=0.12, n_iter_no_change=30)
    m.fit(X, yv)
    prob = m.predict_proba(te[feats].to_numpy(float))[:, 1]
    out = te.copy()
    out["score"] = prob
    auc = float(roc_auc_score(te[label], prob)) if te[label].nunique() > 1 else float("nan")
    metrics = {"auc": round(auc, 4), "train_rows": int(len(tr)), "test_rows": int(len(te)),
               "train_dates": int(tr["date"].nunique()), "test_dates": int(te["date"].nunique()),
               "pos_train": int(yv.sum()), "pos_test": int(te[label].sum()),
               "base_train_pct": round(float(yv.mean()) * 100, 2)}
    for k in (1, 3, 5, 10):
        metrics[f"top{k}"] = precision_at_k(out, "score", k, label)
    return {"model": m, "metrics": metrics, "feats": feats,
            "cut": cut, "label": label, "test": out}


def main() -> int:
    ap_argv = sys.argv[1:]
    cfg = config_mod.load()
    path = cfg.path("picks_dir").parent / "train" / "queue_train.csv"
    df = pd.read_csv(path, low_memory=False)
    df = df.sort_values(["date", "symbol"]).reset_index(drop=True)
    dates = sorted(df["date"].unique())
    holdout = 10
    cut = dates[-holdout] if len(dates) > holdout else dates[0]
    print(f"rows={len(df)} dates={len(dates)} cut={cut}")

    full_df = df[df["has_bfe"] == 1].copy()
    full_feats = feature_cols(full_df, allow_intraday=True)
    daily_feats = feature_cols(df, allow_intraday=False)
    print(f"features full={len(full_feats)} daily={len(daily_feats)}")

    res_full = train_one(full_df, full_feats, cut, "y3")
    res_daily = train_one(df, daily_feats, cut, "y3")

    report = []
    for name, res in (("FULL (daily+intraday+burst)", res_full),
                      ("DAILY-ONLY fallback", res_daily)):
        m = res["metrics"]
        report.append("=" * 74)
        report.append(name)
        report.append("=" * 74)
        report.append(f"cut={res['cut']}  train={m['train_rows']} rows / {m['train_dates']} dates"
                      f"  test={m['test_rows']} rows / {m['test_dates']} dates")
        report.append(f"pos train={m['pos_train']} ({m['base_train_pct']}%)  "
                      f"pos test={m['pos_test']}")
        report.append(f"test AUC (y3) = {m['auc']}")
        report.append("")
        report.append("k   picks  hits  prec(day-avg)  prec(pooled)  base%   lift  recall%")
        for k in (1, 3, 5, 10):
            t = m[f"top{k}"]
            report.append(f"{t['k']:<3} {t['picks']:<6} {t['hits']:<5} "
                          f"{t['precision_dayavg']:<14} {t['precision_pooled']:<12} "
                          f"{t['base_rate']:<7} {t['lift']:<5} {t['recall']}")
        report.append("")
    text = "\n".join(report)
    print(text)

    out_dir = cfg.path("picks_dir").parent / "models"
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "version": 3,
        "label": "y3 = ceiling-locked run of >=3 sessions starting tomorrow",
        "cut": cut,
        "full": {"model": res_full["model"], "features": full_feats,
                 "metrics": res_full["metrics"]},
        "daily": {"model": res_daily["model"], "features": daily_feats,
                  "metrics": res_daily["metrics"]},
    }
    with (out_dir / "queue_v3.pkl").open("wb") as fh:
        pickle.dump(bundle, fh)
    (out_dir / "queue_v3_report.txt").write_text(text, encoding="utf-8")
    (out_dir / "queue_v3_metrics.json").write_text(
        json.dumps({"cut": cut,
                    "full": res_full["metrics"],
                    "daily": res_daily["metrics"]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {out_dir / 'queue_v3.pkl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
