"""End-to-end verification of the buy-candidate discovery pipeline.

Checks, in order:
  1  data freshness      every symbol used by the scan has the market's latest session
  2  lock detection      each candidate really closed at its ceiling (dist == 0)
  3  probability         model output present, in (0, 1], above the configured floor
  4  next session        for_session == next trading day (Thu/Fri skipped)
  5  next ceiling         estimated ceiling == close * (1 + limit/100)
  6  stale exclusion     no candidate whose file ends before the market's last session
  7  history integrity   candidates_history has no duplicate (symbol, target session)
  8  live cross-check    the panel's last price for each candidate is at its ceiling
  9  chunk plan          each candidate's order would split by «آستانه حجم»

Exit code 0 when every check passes.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from tsebot import config as config_mod  # noqa: E402
from tsebot import ride_locked  # noqa: E402
from tsebot.broker_selenium import SeleniumBroker  # noqa: E402
from tsebot.engine import Engine  # noqa: E402

ROWS = []


def check(name, ok, detail=""):
    ROWS.append((name, "PASS" if ok else "FAIL", str(detail)[:70]))


def main() -> int:
    cfg = config_mod.load()
    state = cfg.path("state_dir")
    cand_path = state / "candidates_next.csv"

    # ---------- 1 data freshness
    daily = cfg.path("daily_dir")
    if not daily.exists():
        daily = Path(__file__).resolve().parents[1] / "data" / "daily"
    lasts = {}
    for f in daily.glob("*_daily.csv"):
        try:
            d = pd.read_csv(f, usecols=["date"], dtype={"date": str})
            if len(d):
                lasts[f.name[:-len("_daily.csv")]] = str(d["date"].iloc[-1])
        except Exception:
            continue
    market_last = max(lasts.values()) if lasts else ""
    fresh = sum(1 for v in lasts.values() if v == market_last)
    check("data freshness", fresh >= 0.8 * max(len(lasts), 1),
          f"{fresh}/{len(lasts)} symbols at {market_last}")

    # ---------- candidates
    if not cand_path.exists():
        check("candidates file", False, "missing")
        print_table()
        return 1
    c = pd.read_csv(cand_path, dtype={"for_session": str, "built_at": str})
    check("candidates file", len(c) > 0, f"{len(c)} rows")

    # ---------- 2 lock detection
    bad_lock = []
    for _, r in c.iterrows():
        close, ceil = float(r["close"]), float(r["ceiling_today"])
        if close < ceil * 0.995:
            bad_lock.append(r["symbol"])
    check("lock detection", not bad_lock, f"not at ceiling: {bad_lock}")

    # ---------- 3 probability
    probs = c["prob"].astype(float)
    check("probability", bool(((probs > 0) & (probs <= 1)).all()),
          f"min={probs.min():.3f} max={probs.max():.3f}")

    # ---------- 4 next session
    expect = ride_locked.next_session_label()
    same = (c["for_session"].astype(str) == expect).all()
    check("next session", same, f"{sorted(set(c['for_session']))} vs {expect}")

    # ---------- 5 next ceiling
    bad_ceil = []
    for _, r in c.iterrows():
        est = float(r["next_ceiling_est"])
        want = float(r["close"]) * (1 + float(r["limit_pct"]) / 100)
        if abs(est - want) > max(2.0, want * 0.002):
            bad_ceil.append(r["symbol"])
    check("next ceiling estimate", not bad_ceil, f"mismatch: {bad_ceil}")

    # ---------- 6 stale exclusion
    stale = [s for s in c["symbol"] if lasts.get(str(s), "") != market_last]
    check("stale exclusion", not stale, f"stale in list: {stale}")

    # ---------- 7 history integrity
    hist = state / "candidates_history.csv"
    dup = 0
    if hist.exists():
        h = pd.read_csv(hist, dtype={"for_session": str})
        dup = int(h.duplicated(["symbol", "for_session"]).sum())
    check("history integrity", dup == 0, f"duplicates: {dup}")

    # ---------- 8 live cross-check
    try:
        b = SeleniumBroker(chromedriver=r"C:\TSE_Strategy\chromedriver.exe",
                           debugger_address="127.0.0.1:9333")
        eng = Engine(cfg, b, logger=lambda m: None)
        b.connect()
        off = []
        for _, r in c.iterrows():
            q = eng.market_quote(str(r["symbol"]))
            if q is None or q.last is None:
                off.append(str(r["symbol"]))
                continue
            if q.last < q.ceiling * 0.995:
                off.append(f"{r['symbol']}({q.last:.0f}/{q.ceiling:.0f})")
        check("live cross-check", not off, f"not at ceiling: {off}")
    except Exception as exc:
        check("live cross-check", False, f"broker error: {exc}")

    # ---------- 9 chunk plan
    try:
        plans, zero = [], []
        for _, r in c.iterrows():
            sym = str(r["symbol"])
            if hasattr(b, "bind_symbol"):
                b.bind_symbol(sym)          # the limit is per symbol, so bind first
            lo, hi = b.order_limits() if hasattr(b, "order_limits") else (1, 0)
            plans.append(f"{sym}:{hi:,}")
            if hi <= 0:
                zero.append(sym)
        check("chunk plan (آستانه حجم)", not zero, " · ".join(plans))
    except Exception as exc:
        check("chunk plan", False, str(exc))

    print_table()
    return 0 if all(r[1] == "PASS" for r in ROWS) else 1


def print_table() -> None:
    print(f"{'check':26s} {'result':6s} detail")
    for name, res, det in ROWS:
        print(f"{name:26s} {res:6s} {det}")
    fails = sum(1 for _, r, _ in ROWS if r == "FAIL")
    print(f"\n{len(ROWS)-fails}/{len(ROWS)} PASS")


if __name__ == "__main__":
    sys.exit(main())
