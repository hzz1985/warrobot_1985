# -*- coding: utf-8 -*-
"""Refresh daily OHLCV files from TSE data (algotik_tse), append-only.

Mirrors the one thing v1's nightly job did correctly (merge by date, keep last)
but with three fixes:
  * a single, explicit date convention (fetch Gregorian, store Jalali);
  * it never rewrites a symbol file when the fetch fails;
  * ``--limit`` and ``--dry-run`` make it safe to test.

Usage:
    python scripts\\update_data.py --limit 5 --dry-run
    python scripts\\update_data.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tsebot import config as config_mod  # noqa: E402
from tsebot.calendar_ir import (TradingCalendar, from_jalali, jalali_str,  # noqa: E402
                                now_tehran, to_jalali)


def _last_jalali(df: pd.DataFrame) -> str:
    return str(df["date"].astype(str).max())


def update_symbol(sym: str, path: Path, cal: TradingCalendar, target_g: date,
                  dry: bool) -> str:
    try:
        df = pd.read_csv(path)
        df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
        for c in ("Open", "High", "Low", "Close", "Volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["date"] = df["date"].astype(str)
    except Exception as exc:
        return f"read_err:{exc}"

    last_j = _last_jalali(df)
    try:
        last_g = from_jalali(last_j)
    except Exception:
        return "bad_last_date"
    if last_g >= target_g:
        return "up_to_date"

    start = (last_g - timedelta(days=3)).isoformat()
    end = target_g.isoformat()
    try:
        import algotik_tse as att
        new = att.get_history(symbol=sym, start=start, end=end,
                              date_format="gregorian", progress=False)
    except Exception as exc:
        return f"api_err:{str(exc)[:80]}"
    if new is None or len(new) == 0:
        return "empty"

    new = new.reset_index()
    date_col = None
    for c in new.columns:
        if str(c).lower() in ("date", "index", "datetime"):
            date_col = c
            break
    if date_col is None:
        return "no_date_col"

    rows = []
    for _, r in new.iterrows():
        g = r[date_col]
        try:
            if isinstance(g, str):
                gd = datetime.fromisoformat(g).date()
            elif hasattr(g, "date"):
                gd = g.date()
            else:
                continue
            rows.append({
                "date": to_jalali(gd).strftime("%Y-%m-%d"),
                "Open": float(r.get("Open", r.get("open", 0)) or 0),
                "High": float(r.get("High", r.get("high", 0)) or 0),
                "Low": float(r.get("Low", r.get("low", 0)) or 0),
                "Close": float(r.get("Close", r.get("close", 0)) or 0),
                "Volume": float(r.get("Volume", r.get("volume", 0)) or 0),
            })
        except Exception:
            continue
    if not rows:
        return "no_rows"
    add = pd.DataFrame(rows)
    merged = pd.concat([df[["date", "Open", "High", "Low", "Close", "Volume"]], add])
    merged = merged.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    n_new = len(merged) - len(df)
    if not dry:
        merged.to_csv(path, index=False, encoding="utf-8-sig")
    return f"ok:+{n_new}" if n_new > 0 else "nothing_new"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default="", help="target Gregorian day (default: today)")
    args = ap.parse_args()

    cfg = config_mod.load()
    data_dir = cfg.path("market_data_dir")
    cal = TradingCalendar(holidays_path=cfg.path("state_dir") / "holidays.json")
    target = date.fromisoformat(args.date) if args.date else now_tehran().date()
    # If today is not a trading day, the newest bar is the previous session.
    if not cal.is_trading_day(target)[0]:
        target = cal.last_trading_day(target)

    files = sorted(data_dir.glob("*_daily.csv"))
    if args.symbols:
        want = {s.strip() for s in args.symbols.split(",") if s.strip()}
        files = [f for f in files if f.name[: -len("_daily.csv")] in want]
    if args.limit:
        files = files[: args.limit]

    print(f"target session: {target.isoformat()} ({jalali_str(target)})  files={len(files)}  dry={args.dry_run}")
    stats: dict[str, int] = {}
    for i, f in enumerate(files, 1):
        sym = f.name[: -len("_daily.csv")]
        res = update_symbol(sym, f, cal, target, args.dry_run)
        key = res.split(":")[0]
        stats[key] = stats.get(key, 0) + 1
        if res.startswith("ok:") and i % 20 == 0:
            print(f"  [{i}/{len(files)}] {sym} {res}")
    print("summary:", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())

