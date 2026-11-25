"""Fetch full daily price history for TSE funds (ETF/صندوق).

cdn.tsetmc.com returns the whole series when the page argument is 0:
    /api/ClosingPrice/GetClosingPriceDailyList/{insCode}/0
Saved to data/funds/<symbol>.csv with date (Jalali) and close.
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
OUT = BASE / "data" / "funds"
sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))

FUNDS = {
    "طلا": None, "عیار": None, "افق": None, "اعتماد": None,
    "اهرم": None, "دارا یکم": None, "پالایش": None,
}


def fetch(code: str) -> list[dict]:
    url = f"https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{code}/0"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    rows = (json.loads(raw) or {}).get("closingPriceDaily") or []
    out = []
    for r in rows:
        de = str(r.get("dEven") or "")
        if len(de) != 8:
            continue
        close = r.get("pClosing") or r.get("pDrCotVal") or 0
        out.append({"date": f"{de[:4]}-{de[4:6]}-{de[6:]}", "close": float(close)})
    out.sort(key=lambda x: x["date"])
    return out


def main() -> int:
    import tsetmc as api

    OUT.mkdir(parents=True, exist_ok=True)
    for name in FUNDS:
        try:
            inst = api.resolve(name)
            if not inst:
                print(f"{name}: not resolvable")
                continue
            rows = fetch(inst["ins_code"])
            fp = OUT / f"{name.replace(' ', '_')}.csv"
            with fp.open("w", newline="", encoding="utf-8-sig") as fh:
                w = csv.DictWriter(fh, fieldnames=["date", "close"])
                w.writeheader()
                w.writerows(rows)
            if rows:
                print(f"{name}: {len(rows):5d} rows · {rows[0]['date']} -> {rows[-1]['date']} · last {rows[-1]['close']:,.0f}")
            time.sleep(1.2)
        except Exception as exc:
            print(f"{name}: FAIL {str(exc)[:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
