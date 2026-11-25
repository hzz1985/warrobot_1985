"""Download the FULL daily history for every stock and fund.

The cdn.tsetmc.com endpoint returns the whole series when the page argument is 0:
    /api/ClosingPrice/GetClosingPriceDailyList/{insCode}/0
That fills the 1398-1400 gap the equal-weight index had with the 3-year folder.

Writes data/daily_full/<symbol>.csv with Jalali date, open, high, low, close,
volume and value. Resumable: symbols already present are skipped.
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
OUT = BASE / "data" / "daily_full"
D3Y = BASE.parent / "Tse2" / "orderflow" / "data" / "daily3y"
DAILY = BASE.parent / "TeseRobot" / "data" / "daily"
API = "https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{code}/0"

sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))


def fetch(code: str) -> list[dict]:
    req = urllib.request.Request(API.format(code=code), headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    rows = (json.loads(raw) or {}).get("closingPriceDaily") or []
    out = []
    for r in rows:
        de = str(r.get("dEven") or "")
        if len(de) != 8:
            continue
        out.append({
            "date": f"{de[:4]}-{de[4:6]}-{de[6:]}",
            "open": float(r.get("priceFirst") or 0),
            "high": float(r.get("priceMax") or 0),
            "low": float(r.get("priceMin") or 0),
            "close": float(r.get("pClosing") or r.get("pDrCotVal") or 0),
            "volume": float(r.get("qTotTran5J") or 0),
            "value": float(r.get("qTotCap") or 0),
        })
    out.sort(key=lambda x: x["date"])
    return out


def main() -> int:
    import tsetmc as api

    OUT.mkdir(parents=True, exist_ok=True)
    names = sorted({f.name[:-len("_daily.csv")] for f in DAILY.glob("*_daily.csv")} |
                   {f.stem for f in D3Y.glob("*.csv")})
    done = ok = fail = 0
    for name in names:
        fp = OUT / f"{name}.csv"
        if fp.exists() and fp.stat().st_size > 200:
            done += 1
            continue
        try:
            inst = api.resolve(name)
            if not inst:
                fail += 1
                continue
            rows = fetch(inst["ins_code"])
            if not rows:
                fail += 1
                continue
            with fp.open("w", newline="", encoding="utf-8-sig") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            ok += 1
            if ok % 25 == 0:
                print(f"  ... {ok} downloaded (skipped {done}, failed {fail})", flush=True)
        except Exception:
            fail += 1
        time.sleep(0.35)
    print(f"done: downloaded={ok} skipped={done} failed={fail} of {len(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
