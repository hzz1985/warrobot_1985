"""Weekly review of the nightly candidates vs what actually happened.

Reads state/candidates_history.csv (written by the nightly scan) and the bot's
daily files, then for every logged candidate computes:
  * did the next session close locked at the ceiling?
  * if entered at that session's open (queue entry), what was the net return
    after riding until the first unlock (max 7 sessions)?
Finally it groups everything by the entry-distance band so we can see whether the
"smaller distance = more durable" rule holds in live data.

Usage: python scripts/review_candidates.py [--min-date 1405-07-28]
"""
import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from tsebot import config as config_mod  # noqa: E402

FEES = 2.0
BANDS = [(0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 100)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-date", default="")
    args = ap.parse_args()

    cfg = config_mod.load()
    state = cfg.path("state_dir")
    hist = state / "candidates_history.csv"
    if not hist.exists():
        print("no history yet:", hist)
        return
    h = pd.read_csv(hist, dtype={"logged_at": str})
    if args.min_date:
        h = h[h["logged_at"] >= args.min_date]

    daily_dir = Path(str(cfg.path("daily_dir")))
    if not daily_dir.exists():
        daily_dir = Path(__file__).resolve().parents[1] / "data" / "daily"

    rows = []
    for _, r in h.iterrows():
        sym = str(r["symbol"])
        f = daily_dir / f"{sym}_daily.csv"
        if not f.exists():
            continue
        d = pd.read_csv(f, dtype={"date": str}).sort_values("date").reset_index(drop=True)
        close = d["Close"].astype(float).to_numpy()
        op = d["Open"].astype(float).to_numpy()
        prev = pd.Series(close).shift(1).to_numpy()
        limit = float(r.get("limit_pct") or 5.0)
        ceil = prev * (1 + limit / 100)
        locked = (close >= ceil * 0.999).astype(int)
        # the logged candidate is for the NEXT session: find its index
        target_close = float(r.get("close") or 0)
        idx = None
        for i in range(len(d) - 1, -1, -1):
            if abs(close[i] - target_close) < 1:
                idx = i
                break
        if idx is None or idx + 1 >= len(d):
            continue
        j = idx + 1
        entry = op[j]
        held, k = 0, j
        while k < len(d) and held < 7 and locked[k] == 1:
            held += 1
            k += 1
        exit_i = min(k, len(d) - 1)
        net = (close[exit_i] / entry - 1) * 100 - FEES
        rows.append({"symbol": sym, "logged_at": r["logged_at"], "dist": float(r.get("dist") or 0),
                     "next_locked": int(locked[j]), "held": held, "net": round(net, 2)})

    if not rows:
        print("no completed candidates yet (the next session has not been loaded).")
        return
    t = pd.DataFrame(rows)
    print(t.to_string(index=False))
    print("\n--- by entry-distance band ---")
    for lo, hi in BANDS:
        s = t[(t["dist"] >= lo) & (t["dist"] < hi)]
        if s.empty:
            continue
        print(f"{lo:>4}-{hi:<4}%  n={len(s):3d}  next_locked={s['next_locked'].mean()*100:5.1f}%  "
              f"win={(s['net'] > 0).mean()*100:5.1f}%  avg={s['net'].mean():+.2f}%  "
              f"median_hold={statistics.median(s['held']):.1f}")


if __name__ == "__main__":
    main()
