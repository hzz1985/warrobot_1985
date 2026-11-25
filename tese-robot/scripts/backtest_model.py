# -*- coding: utf-8 -*-
"""Walk-forward backtest of the durable-queue MODEL (not of a hand rule).

For every test date the model is refit only on strictly earlier dates and then
scores that day's universe. The top-k names are bought at the next open (if not
already locked) and managed with the same exit policy as the live bot.

This is the only honest way to answer "does the model make money", because the
model is retrained on a schedule exactly like production.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tsebot import config as config_mod  # noqa: E402
from tsebot.backtest import _simulate_trade  # noqa: E402
from tsebot.signals import load_panel, prepare_panel  # noqa: E402

LABELS = {"y3", "y5", "y7", "q_days"}
META = {"symbol", "date", "feature_source", "has_intraday", "has_bfe"}
EXCLUDE = {"close", "prev_close", "limit_up"}


def feats_of(df: pd.DataFrame, full: bool) -> list[str]:
    cols = []
    for c in df.columns:
        if c in LABELS or c in META or c in EXCLUDE or df[c].dtype == object:
            continue
        if not full and (c.startswith("intr_") or c in
                         {"flk", "flc", "fl60", "flk60", "hs30", "hs60", "pv30",
                          "mhi", "nb1", "nb2", "mxr", "tr", "volM"}):
            continue
        cols.append(c)
    return cols


def main() -> int:
    cfg = config_mod.load()
    bt = cfg.section("backtest")
    top_k = int(bt.get("top_n", 3))
    thr = float(sys.argv[1]) if len(sys.argv) > 1 else float(cfg.get("buy.min_score", 0.5))
    mode = sys.argv[2] if len(sys.argv) > 2 else "full"
    test_days = int(sys.argv[3]) if len(sys.argv) > 3 else 25

    path = cfg.path("picks_dir").parent / "train" / "queue_train.csv"
    df = pd.read_csv(path, low_memory=False).sort_values(["date", "symbol"])
    if mode == "full":
        df = df[df["has_bfe"] == 1].copy()
    feats = feats_of(df, full=(mode == "full"))
    dates = sorted(df["date"].unique())
    test_dates = dates[-test_days:]
    print(f"mode={mode} rows={len(df)} feats={len(feats)} test_dates={len(test_dates)} "
          f"({test_dates[0]}..{test_dates[-1]}) thr={thr} top_k={top_k}")

    panel = load_panel(cfg.path("market_data_dir"))
    max_pos = int(cfg.get("portfolio.max_positions", 3))
    fee_buy = float(cfg.get("fees.buy_pct", 0.6)) / 100.0
    fee_sell = float(cfg.get("fees.sell_pct", 0.6)) / 100.0
    slip = float(bt.get("slippage_pct", 0.3)) / 100.0

    # ---------------------------------------------------- walk-forward scoring
    picks = []          # (date, symbol, score, q_days)
    model = None
    last_fit = None
    for i, d in enumerate(test_dates):
        tr = df[df["date"] < d]
        if len(tr) < 2000 or tr["y3"].sum() < 30:
            continue
        if model is None or last_fit is None or (i % 5 == 0):
            model = HistGradientBoostingClassifier(
                max_iter=300, learning_rate=0.06, max_depth=5, min_samples_leaf=30,
                l2_regularization=1.0, class_weight="balanced", random_state=42)
            model.fit(tr[feats].to_numpy(float), tr["y3"].to_numpy(int))
            last_fit = d
        day = df[df["date"] == d]
        if day.empty:
            continue
        prob = model.predict_proba(day[feats].to_numpy(float))[:, 1]
        day = day.assign(score=prob)
        top = day.nlargest(top_k, "score")
        for _, r in top.iterrows():
            if r["score"] >= thr:
                picks.append((d, r["symbol"], float(r["score"]), int(r["q_days"])))
    print(f"picks generated={len(picks)}")

    # --------------------------------------------------------- trade simulation
    trades = []
    for d, sym, sc, qd in picks:
        p = prepare_panel({sym: panel[sym]})[sym] if sym in panel else None
        if p is None:
            continue
        idx = [k for k, x in enumerate(p["dates"]) if x > d]
        if not idx:
            continue
        ei = idx[0]
        row = panel[sym].iloc[ei]
        entry_open = float(row["Open"])
        prev_close = float(panel[sym].iloc[ei - 1]["Close"])
        limit_up = prev_close * 1.05
        if entry_open >= limit_up * 0.999:
            trades.append({"symbol": sym, "signal_day": d, "entry_day": str(row["date"]),
                           "exit_day": str(row["date"]), "ret_pct": 0.0, "reason": "missed_locked",
                           "days": 0, "score": sc, "queue_days": qd, "entry": 0.0, "exit": 0.0})
            continue
        entry = entry_open * (1 + slip)
        ret, days, reason, ex_px, ex_day = _simulate_trade(panel[sym], ei, entry, cfg)
        net = (1 + ret / 100.0) * (1 - fee_sell) / (1 + fee_buy)
        trades.append({"symbol": sym, "signal_day": d, "entry_day": str(row["date"]),
                       "exit_day": ex_day, "ret_pct": (net - 1) * 100, "reason": reason,
                       "days": days, "score": sc, "queue_days": qd,
                       "entry": entry, "exit": ex_px})

    # concurrency admission
    accepted = []
    for t in sorted([x for x in trades if x["reason"] != "missed_locked"],
                    key=lambda x: (x["entry_day"], -x["ret_pct"])):
        overlap = [a for a in accepted if a["entry_day"] <= t["entry_day"] <= a["exit_day"]]
        if len(overlap) >= max_pos:
            continue
        if any(a["symbol"] == t["symbol"] for a in overlap):
            continue
        accepted.append(t)
    missed = sum(1 for x in trades if x["reason"] == "missed_locked")
    eq, peak, mdd = 1.0, 1.0, 0.0
    for t in sorted(accepted, key=lambda x: (x["exit_day"], x["entry_day"])):
        eq += (eq / max_pos) * t["ret_pct"] / 100.0
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    rets = np.array([t["ret_pct"] for t in accepted])
    hits = sum(1 for t in accepted if t["reason"] == "queue_break")
    report = {
        "mode": mode, "threshold": thr, "test_dates": len(test_dates),
        "picks": len(picks), "missed_locked": missed, "trades": len(accepted),
        "win_rate": round(float((rets > 0).mean() * 100), 1) if len(rets) else 0,
        "avg_pct": round(float(rets.mean()), 3) if len(rets) else 0,
        "median_pct": round(float(np.median(rets)), 3) if len(rets) else 0,
        "total_return_pct": round((eq - 1) * 100, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "queue_ride_exits": hits,
        "by_reason": pd.Series([t["reason"] for t in accepted]).value_counts().to_dict(),
        "avg_queue_days": round(float(np.mean([t["queue_days"] for t in accepted])), 2) if accepted else 0,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    out = cfg.path("report_dir") / f"backtest_model_{mode}_{test_days}d.json"
    out.write_text(json.dumps({"report": report, "trades": accepted}, ensure_ascii=False,
                              indent=2, default=str), encoding="utf-8")
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

