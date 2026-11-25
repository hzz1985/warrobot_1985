"""Fetch the macro series the allocation model needs.

Source: tgju's public summary-table endpoint, which returns daily OHLC rows
(newest first) together with both the Gregorian and the Jalali date.

Saved to data/macro/<key>.csv with columns: date (Gregorian), close, open, low, high.
"""
from __future__ import annotations

import csv
import json
import re
import urllib.request
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
OUT = BASE / "data" / "macro"
API = "https://api.tgju.org/v1/market/indicator/summary-table-data/{key}"
KEYS = {"usd": "price_dollar_rl", "gold18": "geram18", "mesghal": "mesghal"}


def _num(s: str) -> float:
    s = re.sub(r"<[^>]+>", "", str(s or "")).replace(",", "").strip()
    try:
        return float(s)
    except Exception:
        return 0.0


def fetch(key: str) -> list[dict]:
    req = urllib.request.Request(API.format(key=key), headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    data = json.loads(raw).get("data") or []
    rows = []
    for r in data:
        v = r.get("value") if isinstance(r, dict) else r
        if not v or len(v) < 8:
            continue
        # [open, low, high, close, change, change%, gregorian, jalali]
        rows.append({"date": str(v[6]).replace("/", "-"),
                     "close": _num(v[3]), "open": _num(v[0]),
                     "low": _num(v[1]), "high": _num(v[2]),
                     "jalali": str(v[7])})
    rows.sort(key=lambda x: x["date"])
    return rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, key in KEYS.items():
        try:
            rows = fetch(key)
        except Exception as exc:
            print(f"{name}: FAIL {str(exc)[:70]}")
            continue
        fp = OUT / f"{name}.csv"
        with fp.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=["date", "close", "open", "low", "high", "jalali"])
            w.writeheader()
            w.writerows(rows)
        if rows:
            print(f"{name}: {len(rows):5d} rows · {rows[0]['date']} -> {rows[-1]['date']} · "
                  f"last close {rows[-1]['close']:,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
