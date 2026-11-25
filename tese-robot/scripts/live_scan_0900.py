"""09:00 live scan: find stocks that are locked at the ceiling RIGHT NOW.

Runs at the open, before the 09:01 buy:
  1. universe = last night's candidates + every symbol that closed locked yesterday
  2. for each, fetch the live 5-level book (BestLimits) in parallel
  3. keep the ones with no seller at the ceiling (ask side empty at the top) and a
     buy queue worth at least `min_queue_value_rial`
  4. write state/live_candidates.csv and email the list

The distance-to-ceiling filter (0.25%) is applied against the ceiling computed
from yesterday's close, so the same rule as the backtest winner is used.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot")
OF = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\Tse2\orderflow")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(OF))

import tsetmc as api                      # noqa: E402
from tsebot import config as config_mod   # noqa: E402
from tsebot.alerts import send            # noqa: E402
from tsebot.calendar_ir import now_tehran  # noqa: E402

DAILY = ROOT / "data" / "daily"
STATE = ROOT / "state" / "live_candidates.csv"


def universe(cfg) -> pd.DataFrame:
    """Candidates from last night plus every symbol locked in the last session."""
    frames = []
    cand = ROOT / "state" / "candidates_next.csv"
    if cand.exists():
        try:
            d = pd.read_csv(cand, dtype={"for_session": str})
            d["source"] = "nightly"
            frames.append(d[["symbol", "prob", "close", "ceiling_today", "limit_pct", "source"]])
        except Exception:
            pass
    last_date = max((pd.read_csv(f, usecols=["date"], dtype={"date": str})["date"].iloc[-1]
                     for f in DAILY.glob("*_daily.csv")), default="")
    rows = []
    for f in DAILY.glob("*_daily.csv"):
        try:
            d = pd.read_csv(f, dtype={"date": str})
        except Exception:
            continue
        if len(d) < 25 or str(d["date"].iloc[-1]) != last_date:
            continue
        d = d.sort_values("date").reset_index(drop=True)
        lim = 3.0
        prev = float(d["Close"].iloc[-2])
        ceil = round(prev * (1 + lim / 100))
        close = float(d["Close"].iloc[-1])
        if close >= ceil * 0.999:
            rows.append({"symbol": f.name[:-len("_daily.csv")], "prob": None, "close": close,
                         "ceiling_today": ceil, "limit_pct": lim, "source": "locked"})
    if rows:
        frames.append(pd.DataFrame(rows))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates("symbol")


def probe(row, min_qty_rial: float) -> dict:
    inst = api.resolve(row["symbol"])
    if not inst:
        return {"symbol": row["symbol"], "ok": False, "why": "no_inscode"}
    b = api.parse_book(api.best_limits(inst["ins_code"]))
    bid, ask = b["best_bid"], b["best_ask"]
    qty = b["bid_volume_5"]
    value = qty * (bid or 0)
    ceiling = float(row["ceiling_today"] or 0)
    locked = bool(bid and (not ask) and ceiling and abs(bid - ceiling) <= max(1.0, ceiling * 0.0025))
    return {"symbol": row["symbol"], "ok": True, "bid": bid, "ask": ask,
            "queue_qty": qty, "queue_value": value, "ceiling": ceiling,
            "locked": locked, "why": "" if locked else ("ask_present" if ask else "below_ceiling"),
            "prob": row.get("prob"), "source": row.get("source")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--min-queue-m", type=float, default=300.0)  # million rial
    ap.add_argument("--no-email", action="store_true")
    a = ap.parse_args()

    cfg = config_mod.load()
    uni = universe(cfg)
    if uni.empty:
        print("empty universe")
        return 1
    print(f"probing {len(uni)} symbols…", flush=True)
    out = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for r in ex.map(lambda row: probe(row, a.min_queue_m * 1e6), uni.to_dict("records")):
            out.append(r)
    df = pd.DataFrame(out)
    live = df[(df.ok) & (df.locked) & (df.queue_value >= a.min_queue_m * 1e6)].copy()
    if not live.empty:
        live = live.sort_values(["prob", "queue_value"], ascending=False).head(3)
    live["for_date"] = str(uni["source"].iloc[0]) if False else ""
    live.to_csv(STATE, index=False, encoding="utf-8-sig")

    lines = [f"اسکن زندهٔ ساعت {now_tehran().strftime('%H:%M:%S')}",
             f"نمادهای بررسی‌شده: {len(df)} · قفل‌شده با صف کافی: {len(live)}", ""]
    if len(live):
        lines.append("کاندیدهای آمادهٔ خرید:")
        for _, r in live.iterrows():
            lines.append(f"  {r['symbol']}: صف {r['queue_value']/1e6:,.0f} م.ریال · "
                         f"بهترین خرید {r['bid']:,.0f} · سقف {r['ceiling']:,.0f}"
                         + (f" · احتمال {r['prob']:.3f}" if pd.notna(r["prob"]) else ""))
        lines += ["", "خرید در ۰۹:۰۱ روی قیمت سقف انجام میشود."]
    else:
        lines.append("هیچ نمادی شرایط قفل + صف را ندارد؛ خریدی انجام نمیشود.")
    body = "\n".join(lines)
    print(body)
    if not a.no_email:
        send(f"[TeseRobot] اسکن زندهٔ {now_tehran().strftime('%H:%M')} — {len(live)} کاندید",
             body, key=f"livescan:{now_tehran().strftime('%Y-%m-%d')}", force=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
