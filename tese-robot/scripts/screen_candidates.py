"""Stage 1b — screen flow features on the REAL candidate set, with the queue exit.

Fixes the flaw of the naive screen: outcomes are simulated with the strategy's own
exit (ride the ceiling lock; leave on the first unlocked session), and the screen
runs only on the symbols the trained model actually picks.

Usage:  python scripts/screen_candidates.py
Output: reports/candidate_screen.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pickle

ROOT = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot")
BE = 1.0089
STOP = 3.0
HOLD = 10
FEE = 2.0
TARGETS = (6.0, 9.0, 12.0)
TOPN = 3
MIN_PROB = 0.70


def norm(s):
    return (str(s).replace("ي", "ی").replace("ك", "ک").replace("\u200c", "").strip())


def load_daily():
    out = {}
    for f in (ROOT / "data" / "daily").glob("*_daily.csv"):
        sym = f.name.replace("_daily.csv", "")
        try:
            d = pd.read_csv(f)
        except Exception:
            continue
        if len(d) < 30:
            continue
        out[sym] = {
            "date": d["date"].astype(str).to_numpy(),
            "open": d["Open"].to_numpy(float),
            "high": d["High"].to_numpy(float),
            "low": d["Low"].to_numpy(float),
            "close": d["Close"].to_numpy(float),
        }
    return out


def simulate(bars, idx, target):
    """Net return of a queue-entry trade: buy the next open, hold while the stock
    stays locked at the ceiling, leave on the first session that unlocks."""
    if idx + 2 >= len(bars["date"]):
        return None
    i = idx + 1
    h, l, c = bars["high"], bars["low"], bars["close"]
    entry = bars["open"][i]
    if not entry or entry <= 0:
        return None
    tp = entry * BE * (1 + target / 100.0)
    sl = entry * BE * (1 - STOP / 100.0)
    exit_px = None
    for j in range(i, min(i + HOLD, len(h))):
        ceiling = c[j - 1] * 1.05
        locked_at_ceiling = (h[j] == l[j]) and (c[j] >= ceiling * 0.999)
        if h[j] >= tp:
            exit_px = tp
            break
        if l[j] <= sl and not locked_at_ceiling:
            exit_px = sl
            break
        if not locked_at_ceiling:
            exit_px = c[j]           # queue broke -> exit that session
            break
    if exit_px is None:
        exit_px = c[min(i + HOLD, len(c) - 1)]
    return (exit_px / entry - 1) * 100.0 - FEE


def main():
    bundle = pickle.load(open(ROOT / "models" / "queue_v3.pkl", "rb"))
    model, feats = bundle["full"]["model"], bundle["full"]["features"]

    sig = pd.read_csv(ROOT / "data" / "train" / "queue_train.csv")
    sig["date"] = sig["date"].astype(str)
    X = sig[feats].fillna(0.0)
    sig["prob"] = model.predict_proba(X)[:, 1]

    cand = (sig.sort_values(["date", "prob"], ascending=[True, False])
               .groupby("date").head(TOPN))
    cand = cand[cand["prob"] >= MIN_PROB]
    print(f"candidates: {len(cand):,} over {cand['date'].nunique()} sessions")

    daily = load_daily()
    rows = []
    for _, r in cand.iterrows():
        bars = daily.get(r["symbol"])
        if bars is None:
            continue
        pos = np.where(bars["date"] == r["date"])[0]
        if len(pos) == 0:
            continue
        rec = {"symbol": r["symbol"], "date": r["date"], "prob": r["prob"]}
        ok = True
        for t in TARGETS:
            net = simulate(bars, int(pos[0]), t)
            if net is None:
                ok = False
                break
            rec[f"net{int(t)}"] = net
        if not ok:
            continue
        for f in feats:
            rec[f] = r.get(f)
        rows.append(rec)

    df = pd.DataFrame(rows)
    print(f"simulated trades: {len(df):,}")
    if df.empty:
        return

    print("\n=== strategy outcome on candidates (queue exit) ===")
    for t in TARGETS:
        col = f"net{int(t)}"
        print(f"  target {int(t):2d}%: win={100*(df[col] > 0).mean():.1f}%  "
              f"avg={df[col].mean():+.2f}%  median={df[col].median():+.2f}%  "
              f"n={len(df)}")

    print("\n=== feature screen inside candidates (top quartile vs rest) ===")
    out = []
    for f in feats:
        s = pd.to_numeric(df[f], errors="coerce")
        if s.notna().sum() < 80 or s.nunique() < 4:
            continue
        thr = s.quantile(0.75)
        m = s >= thr
        if m.sum() < 30:
            continue
        rec = {"feature": f, "q75": round(float(thr), 3), "n_top": int(m.sum())}
        for t in TARGETS:
            col = f"net{int(t)}"
            rec[f"win{int(t)}"] = round(100 * (df.loc[m, col] > 0).mean(), 1)
            rec[f"avg{int(t)}"] = round(df.loc[m, col].mean(), 2)
        rec["win6_all"] = round(100 * (df[f"net6"] > 0).mean(), 1)
        rec["avg6_all"] = round(df[f"net6"].mean(), 2)
        out.append(rec)
    tbl = pd.DataFrame(out).sort_values("avg6", ascending=False)
    print(tbl.to_string(index=False))
    tbl.to_csv(ROOT / "reports" / "candidate_screen.csv", index=False, encoding="utf-8")
    print("\nwritten: reports/candidate_screen.csv")


if __name__ == "__main__":
    main()
