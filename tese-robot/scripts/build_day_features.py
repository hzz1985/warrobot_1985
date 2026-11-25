# -*- coding: utf-8 -*-
"""Build ONE day's full feature set (intraday + Burst) for queue_v3.

Why a single fetch: the v1 pipeline fetched trades twice (once for intraday
features, once for Burst) and used a third stale table at scoring time. Here one
`att.get_trades` call per symbol produces both feature blocks, so the live row
is exactly the row the model was trained on.

The intraday formulas are imported verbatim from the proven extractor
(`build_intraday_day.extract_day_features`); only the loop around it is ours.

Usage:
    python scripts\\build_day_features.py --top 120      # nightly (recommended)
    python scripts\\build_day_features.py --limit 3      # smoke test
    python scripts\\build_day_features.py --symbols فولاد,شپاس
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tsebot import config as config_mod  # noqa: E402
from tsebot.queue_features import BFE_COLS, INTRADAY_MAP, burst_features  # noqa: E402

EXTRACTOR = Path(r"C:\TSE_Strategy\build_intraday_day.py")


def load_extractor():
    """Exec only the feature functions of the v1 script (never its main())."""
    src = EXTRACTOR.read_text(encoding="utf-8")
    start = src.index("def t2m")
    end = src.index("def main")
    ns: dict = {"np": __import__("numpy"), "pd": pd}
    exec(src[start:end], ns)
    return ns["extract_day_features"], ns["normalize"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="Jalali YYYY-MM-DD (default: latest daily bar)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--top", type=int, default=0,
                    help="only build features for the top-N daily-model candidates")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    cfg = config_mod.load()
    extract_day_features, normalize = load_extractor()

    if args.date:
        jal = args.date
    else:
        last = ""
        for f in sorted(cfg.path("market_data_dir").glob("*_daily.csv"))[:25]:
            try:
                d = pd.read_csv(f, usecols=["date"])["date"].astype(str).max()
                last = max(last, d)
            except Exception:
                continue
        jal = last
    if not jal:
        print("cannot determine date")
        return 2

    # ---- choose symbols -----------------------------------------------------
    if args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.top:
        from tsebot.signals_v3 import score_day
        scored = score_day(cfg, jal)
        pos = [p.symbol for p in []]
        syms = scored.head(args.top)["symbol"].astype(str).tolist()
    else:
        syms = sorted(f.name[: -len("_daily.csv")]
                      for f in cfg.path("market_data_dir").glob("*_daily.csv"))
    if args.limit:
        syms = syms[: args.limit]
    print(f"date={jal} symbols={len(syms)} workers={args.workers}")

    import algotik_tse as att

    def one(sym: str):
        try:
            raw = att.get_trades(sym, start=jal, end=jal, progress=False)
            if raw is None or len(raw) < 5:
                return sym, None, "empty"
            g = normalize(raw)
            intr = extract_day_features(g)
            bfe = burst_features(g)
            if intr is None or bfe is None:
                return sym, None, "no_features"
            row = {"symbol": sym, "date": jal}
            row.update({INTRADAY_MAP.get(k, "intr_" + k): v for k, v in intr.items()
                        if k in INTRADAY_MAP})
            row.update({k: v for k, v in bfe.items() if k in BFE_COLS})
            return sym, row, "ok"
        except Exception as exc:
            return sym, None, f"err:{str(exc)[:60]}"

    rows, stats = [], {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(one, s): s for s in syms}
        for i, fut in enumerate(as_completed(futs), 1):
            sym, row, st = fut.result()
            key = st.split(":")[0]
            stats[key] = stats.get(key, 0) + 1
            if row:
                rows.append(row)
            if i % 25 == 0:
                print(f"  [{i}/{len(syms)}] ok={stats.get('ok', 0)} t={time.time()-t0:.0f}s")

    out_dir = cfg.path("picks_dir").parent / "features"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"feature_day_{jal}.csv"
    if rows:
        df = pd.DataFrame(rows)
        if dst.exists():
            old = pd.read_csv(dst, low_memory=False)
            df = pd.concat([old[~old["symbol"].isin(df["symbol"])], df], ignore_index=True)
        df.to_csv(dst, index=False, encoding="utf-8-sig")
        print(f"saved {dst} rows={len(df)} symbols={df['symbol'].nunique()}")
    print("summary:", stats, f"elapsed={time.time()-t0:.0f}s")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
