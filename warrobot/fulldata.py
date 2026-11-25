"""Loader for data/daily_full (Gregorian dates) that speaks Jalali like the rest.

The downloader stores the raw Gregorian dEven from TSETMC; everything else in the
project indexes on Jalali dates, so convert once here and cache the result.
"""
from __future__ import annotations

import functools
from pathlib import Path

import jdatetime
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
FULL = BASE / "data" / "daily_full"


def _to_jalali(g: str) -> str:
    try:
        y, m, d = (int(x) for x in str(g).split("-"))
        return jdatetime.date.fromgregorian(year=y, month=m, day=d).strftime("%Y-%m-%d")
    except Exception:
        return ""


@functools.lru_cache(maxsize=1)
def symbols() -> list[str]:
    return sorted(f.stem for f in FULL.glob("*.csv"))


@functools.lru_cache(maxsize=4096)
def load(sym: str) -> pd.DataFrame | None:
    fp = FULL / f"{sym}.csv"
    if not fp.exists():
        return None
    d = pd.read_csv(fp, dtype={"date": str})
    if d.empty:
        return None
    d["jdate"] = d["date"].map(_to_jalali)
    d = d[d["jdate"] != ""].copy()
    for c in ("open", "high", "low", "close", "volume", "value"):
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0)
    d = d.sort_values("jdate").drop_duplicates("jdate", keep="last")
    return d.reset_index(drop=True)


def equal_weight_index(start: str = "1397-01-01") -> pd.Series:
    rets = []
    for s in symbols():
        d = load(s)
        if d is None or len(d) < 120:
            continue
        d = d[d["jdate"] >= start]
        if len(d) < 100:
            continue
        r = d.set_index("jdate")["close"].pct_change().rename(s)
        # raw TSETMC history has unadjusted splits and penny-stock spikes:
        # winsorise so one symbol cannot dominate the index
        r = r.where(r.abs() <= 0.20)
        rets.append(r)
    m = pd.concat(rets, axis=1).median(axis=1).sort_index()
    return (1 + m.fillna(0)).cumprod()


if __name__ == "__main__":
    idx = equal_weight_index()
    print("index sessions:", len(idx), "|", idx.index.min(), "->", idx.index.max())
    print("years:", sorted({d[:4] for d in idx.index}))
