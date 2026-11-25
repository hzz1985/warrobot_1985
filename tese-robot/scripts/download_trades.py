"""Download per-instrument daily trade tables from the exchange.

`algotik_tse.get_market_trades(date=...)` actually returns one aggregated row per
instrument for that session: TradeCount, Volume, Value, OHLC. That is exactly the
microstructure source needed for "ریز معاملات" features (average trade size, trade
count, VWAP) for EVERY symbol and EVERY past session — something the intraday
feature file only covers for a small subset.

Resumable: existing files are skipped.
"""
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot")
OUT = ROOT / "data" / "trades"
OUT.mkdir(parents=True, exist_ok=True)

START = (1404, 4, 1)
END = (1405, 7, 27)


def jalali_days(start, end):
    """Simple Jalali month walker (1..31 each month, invalid dates just return 0 rows)."""
    y, m, d = start
    while (y, m) <= (end[0], end[1]) or (y, m, d) <= end:
        yield f"{y:04d}-{m:02d}-{d:02d}"
        d += 1
        if d > 31:
            d = 1
            m += 1
            if m > 12:
                m = 1
                y += 1
        if (y, m, d) > end:
            break


def main() -> None:
    import algotik_tse as att

    total = saved = skipped = empty = failed = 0
    for day in jalali_days(START, END):
        total += 1
        f = OUT / f"{day}.csv"
        if f.exists():
            skipped += 1
            continue
        try:
            df = att.get_market_trades(date=day)
        except Exception as exc:
            failed += 1
            print(f"{day} ERROR {str(exc)[:60]}", flush=True)
            time.sleep(1)
            continue
        if df is None or len(df) == 0:
            empty += 1
            f.write_text("", encoding="utf-8")
            continue
        df.to_csv(f, index=False, encoding="utf-8")
        saved += 1
        print(f"{day} rows={len(df)}", flush=True)
        time.sleep(0.15)
    print(f"DONE total={total} saved={saved} skipped={skipped} empty={empty} failed={failed}")


if __name__ == "__main__":
    main()
