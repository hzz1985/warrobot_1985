"""Unbiased Type-A event fetch: page through each symbol's Category=2 letters.

Why: the api's FromDate/ToDate filter matches the fiscal PERIOD END, not the
publication date, so the earlier quarterly-window fetch produced a quarter-end
biased sample (94.6% of events landed on days 28-31).

This version queries by Symbol (no date filter), walks every page, and keeps the
real publication datetime. Progress is resumable (state/codal_sym_progress.json).
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
OUT = BASE / "state" / "codal_typeA_bysymbol.csv"
PROG = BASE / "state" / "codal_sym_progress.json"
API = "https://search.codal.ir/api/search/v2/q"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/157.0",
           "Accept": "application/json, text/plain, */*",
           "Referer": "https://search.codal.ir/"}
FIELDS = ["tracing_no", "symbol", "company", "title", "published", "letter_code", "url"]


def get(params: dict, tries: int = 20) -> dict | None:
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(tries):
        try:
            return json.loads(urllib.request.urlopen(
                urllib.request.Request(url, headers=HEADERS), timeout=40).read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = min(420, 90 + attempt * 30)
                print(f"  429 wait {wait}s", flush=True)
                time.sleep(wait)
                continue
            time.sleep(10)
        except Exception as exc:
            print(f"  net {str(exc)[:50]}", flush=True)
            time.sleep(12)
    return None


def seen() -> set:
    keys = set()
    if OUT.exists():
        with OUT.open(encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                keys.add(r["tracing_no"])
    return keys


def append(rows: list[dict]) -> None:
    exists = OUT.exists()
    with OUT.open("a", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def main() -> int:
    events = {r["symbol"] for r in csv.DictReader(
        open(BASE / "state" / "codal_typeA_events.csv", encoding="utf-8-sig"))}
    symbols = sorted(events)
    prog = json.loads(PROG.read_text(encoding="utf-8")) if PROG.exists() else {"idx": 0, "page": 1}
    known = seen()
    total_new = 0
    while prog["idx"] < len(symbols):
        sym = symbols[prog["idx"]]
        page = int(prog.get("page") or 1)
        d = get({"Page": page, "PageSize": 100, "Category": 2, "Symbol": sym})
        if d is None:
            print("window failed, waiting", flush=True)
            time.sleep(120)
            continue
        letters = d.get("Letters") or []
        rows = []
        for L in letters:
            title = str(L.get("Title") or "")
            if "گروه الف" not in title:
                continue
            tid = str(L.get("TracingNo") or "")
            if tid in known:
                continue
            rows.append({"tracing_no": tid, "symbol": L.get("Symbol"),
                         "company": L.get("CompanyName"),
                         "title": title.replace("\n", " ").strip(),
                         "published": (L.get("PublishDateTime") or "").strip(),
                         "letter_code": L.get("LetterCode"),
                         "url": "https://codal.ir" + (L.get("Url") or "")})
        if rows:
            append(rows)
            known.update(r["tracing_no"] for r in rows)
            total_new += len(rows)
        print(f"{sym} page {page}: {len(letters)} letters, +{len(rows)} typeA "
              f"(total new {total_new})", flush=True)
        if not letters or len(letters) < 100 or page >= 100:
            prog["idx"] += 1
            prog["page"] = 1
        else:
            prog["page"] = page + 1
        PROG.write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")
        time.sleep(4)
    print("DONE", prog, "new events:", total_new, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
