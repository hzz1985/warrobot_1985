"""Nightly big/small trade ratio for tomorrow's candidates.

Runs right after the nightly data download. For every band candidate it reads the
LAST SESSION's tape (not today's) and computes the same ratio the research used:

    big trade   : qty >= 5 x median trade size
    ratio       : big_volume_share / small_volume_share

Result -> state/big_small.json   {symbol: {"session": "1405-08-03", "ratio": 1.42}}

At 09:00 the live scan only reads this file (no API call, no look-ahead).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P / "src"))
sys.path.insert(0, str(P.parent / "Tse2" / "orderflow"))

import pandas as pd                      # noqa: E402
import tsetmc as api                     # noqa: E402
import distbot as bot                    # noqa: E402

OUT = P / "state" / "big_small.json"


def ratio_for(ins_code: str, session: str) -> float:
    """big/small volume ratio on `session` (Jalali date string)."""
    import jdatetime
    y, m, d = (int(x) for x in session.split("-"))
    d_even = int(jdatetime.date(y, m, d).togregorian().strftime("%Y%m%d"))
    trades = api.parse_trades(api.trade_history(ins_code, d_even))
    qty = [float(t.get("qty") or t.get("q") or t.get("quantity") or 0) for t in trades]
    qty = [q for q in qty if q > 0]
    if len(qty) < 20:
        return 0.0
    med = sorted(qty)[len(qty) // 2]
    if med <= 0:
        return 0.0
    big = sum(q for q in qty if q >= 5 * med)
    small = sum(qty) - big
    return round(big / small, 3) if small > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    cand = P / "state" / "candidates.csv"
    if not cand.exists():
        print("no candidates")
        return 1
    df = pd.read_csv(cand)
    if df.empty:
        print("empty candidates")
        return 1
    session = str(df["session"].iloc[0])          # the session the band was measured on
    if a.limit:
        df = df.head(a.limit)

    from concurrent.futures import ThreadPoolExecutor

    def one(sym):
        inst = api.resolve(sym)
        if not inst:
            return sym, None
        return sym, ratio_for(inst["ins_code"], session)

    result = {}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for sym, r in ex.map(one, df["symbol"].tolist()):
            if r is not None:
                result[sym] = {"session": session, "ratio": r}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for v in result.values() if v["ratio"] >= 1.0)
    print(f"session {session}: {len(result)} symbols · ratio>=1: {ok} -> {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
