"""Download every "اطلاعیه الف" (group A important disclosure) from codal.ir.

Source (public): https://search.codal.ir/api/search/v2/q
  Category=2  -> افشای اطلاعات بااهمیت
  title filter "گروه الف" -> گروه الف (Type A)

We page through Jalali quarters from 1400/01 to today and store:
  TracingNo, Symbol, CompanyName, Title, PublishDateTime (Jalali), Url
Output: state/codal_typeA_events.csv
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
OUT = BASE / "state" / "codal_typeA_events.csv"
API = "https://search.codal.ir/api/search/v2/q"


def get(params: dict) -> dict:
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(3):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception as exc:
            if attempt == 2:
                raise
            time.sleep(1.5)
    return {}


def windows() -> list[tuple[str, str]]:
    out = []
    for y in range(1400, 1406):
        for q in range(1, 5):
            m0 = (q - 1) * 3 + 1
            m1 = m0 + 2
            # Jalali months have 31/31/31/31/31/31/30/30/30/30/30/29
            last = 31 if m1 <= 6 else 30
            out.append((f"{y}/{m0:02d}/01", f"{y}/{m1:02d}/{last:02d}"))
    # current year up to today
    out.append(("1405/01/01", "1405/08/23"))
    return out


def main() -> int:
    seen = {}
    total_pages = 0
    for frm, to in windows():
        page = 1
        while page <= 60:
            try:
                d = get({"Page": page, "PageSize": 100, "Category": 2,
                         "FromDate": frm, "ToDate": to})
            except Exception as exc:
                print("ERR", frm, to, page, str(exc)[:60])
                break
            total_pages += 1
            letters = d.get("Letters") or []
            if not letters:
                break
            for L in letters:
                title = str(L.get("Title") or "")
                if "گروه الف" not in title:
                    continue
                key = L.get("TracingNo") or L.get("Url")
                seen[key] = {
                    "tracing_no": L.get("TracingNo"),
                    "symbol": L.get("Symbol"),
                    "company": L.get("CompanyName"),
                    "title": title.replace("\n", " ").strip(),
                    "published": (L.get("PublishDateTime") or "").strip(),
                    "letter_code": L.get("LetterCode"),
                    "url": "https://codal.ir" + (L.get("Url") or ""),
                }
            tp = int(d.get("Total") or 0)
            if page * 100 >= tp:
                break
            page += 1
            time.sleep(0.25)
        print(f"window {frm}..{to}: total so far {len(seen)}")
    rows = list(seen.values())
    rows.sort(key=lambda r: r["published"])
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["tracing_no", "symbol", "company", "title",
                                           "published", "letter_code", "url"])
        w.writeheader()
        w.writerows(rows)
    print(f"saved {len(rows)} Type-A events -> {OUT}  (pages fetched: {total_pages})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
