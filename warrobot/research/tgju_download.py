"""Download one year of commodity/currency series from the public TGJU API.

The Telegram post needs these inputs per item:
    domestic certificate price, global reference price, USD/IRR rate,
    physical price, VAT, premium, price-status.

TGJU (api.tgju.org) is reachable and carries the global references and the USD
rate with daily history. Keys verified on 1405/08/23:

    price_dollar_rl  USD/IRR (free market)      ✔
    silver           silver ounce (USD)         ✔
    oil_brent        Brent (USD)                ✔
    zinc             zinc (USD)                 ✔
    lead             lead (USD)                 ✔
    copper           copper (USD)               ✔
    gerami           gold gram (IRR, 18k)       ✔
    mesghal          gold mesghal (IRR)         ✔
    sekeb            coin (IRR)                 ✔

The certificate prices themselves (IME) are NOT on TGJU; they are the missing
piece and are collected daily from the channel post.
"""
from __future__ import annotations

import csv
import json
import sys
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
OUTDIR = BASE / "research" / "data" / "tgju"
API = "https://api.tgju.org/v1/market/indicator/summary-table-data/"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/157.0"}

KEYS = ["price_dollar_rl", "silver", "oil_brent", "zinc", "lead",
        "copper", "gerami", "mesghal", "sekeb"]

# keep the last ~13 months (Jalali 1404/08 .. 1405/08)
MIN_JALALI = "1404/08/01"


def fetch(key: str) -> list[dict]:
    url = API + key
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=40).read()
    d = json.loads(raw)
    rows = []
    for r in d.get("data") or []:
        if len(r) < 8:
            continue
        jalali = r[7].strip()
        if jalali < MIN_JALALI:
            continue
        try:
            val = float(str(r[0]).replace(",", ""))
        except Exception:
            continue
        rows.append({"date_greg": r[6].strip(), "date_jalali": jalali, "value": val})
    rows.sort(key=lambda x: x["date_jalali"])
    return rows


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    summary = {}
    for key in KEYS:
        try:
            rows = fetch(key)
        except Exception as exc:
            print(f"{key}: ERR {str(exc)[:70]}")
            summary[key] = 0
            continue
        if not rows:
            print(f"{key}: no rows in the window")
            summary[key] = 0
            continue
        path = OUTDIR / f"tgju_{key}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["date_greg", "date_jalali", "value"])
            w.writeheader()
            w.writerows(rows)
        summary[key] = len(rows)
        print(f"{key}: {len(rows)} rows  {rows[0]['date_jalali']} .. {rows[-1]['date_jalali']}"
              f"  last={rows[-1]['value']:,}")
    (OUTDIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    print("saved ->", OUTDIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
