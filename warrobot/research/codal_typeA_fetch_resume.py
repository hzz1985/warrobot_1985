"""Resumable, throttled Type-A disclosure fetcher (codal rate limit aware).

codal.ir answers 429 when hit quickly, so this version:
  * keeps a progress file (state/codal_fetch_progress.json) and appends events
  * waits a long time after a 429 and resumes from the same page
  * can be stopped and restarted any time (safe for a background job)

    python research/codal_typeA_fetch_resume.py
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
OUT = BASE / "state" / "codal_typeA_events.csv"
PROG = BASE / "state" / "codal_fetch_progress.json"
API = "https://search.codal.ir/api/search/v2/q"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://search.codal.ir/",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
}
WINDOWS = ["1400/01/01", "1400/04/01", "1400/07/01", "1400/10/01",
           "1401/01/01", "1401/04/01", "1401/07/01", "1401/10/01",
           "1402/01/01", "1402/04/01", "1402/07/01", "1402/10/01",
           "1403/01/01", "1403/04/01", "1403/07/01", "1403/10/01",
           "1404/01/01", "1404/04/01", "1404/07/01", "1404/10/01",
           "1405/01/01"]
END = "1405/08/23"


def _end_of(start: str) -> str:
    y, m, _ = start.split("/")
    m = int(m)
    last = 31 if m + 2 <= 6 else 30
    if m == 10:
        last = 29
    return f"{y}/{m + 2:02d}/{last:02d}"


def load_progress() -> dict:
    try:
        return json.loads(PROG.read_text(encoding="utf-8"))
    except Exception:
        return {"window_idx": 0, "page": 1, "events": 0}


def save_progress(p: dict) -> None:
    PROG.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


def append(rows: list[dict]) -> None:
    exists = OUT.exists()
    with OUT.open("a", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["tracing_no", "symbol", "company", "title",
                                           "published", "letter_code", "url"])
        if not exists:
            w.writeheader()
        w.writerows(rows)


def get_page(params: dict, tries: int = 30) -> dict | None:
    q = urllib.parse.urlencode(params)
    url = f"{API}?{q}"
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            return json.loads(urllib.request.urlopen(req, timeout=40).read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = min(300, 60 + attempt * 20)
                print(f"  429 -> sleeping {wait}s (attempt {attempt + 1})", flush=True)
                time.sleep(wait)
                continue
            print(f"  http {exc.code}", flush=True)
            time.sleep(10)
        except Exception as exc:
            print(f"  net error {str(exc)[:50]}", flush=True)
            time.sleep(15)
    return None


def main() -> int:
    prog = load_progress()
    while prog["window_idx"] < len(WINDOWS):
        start = WINDOWS[prog["window_idx"]]
        end = END if prog["window_idx"] == len(WINDOWS) - 1 else _end_of(start)
        page = int(prog.get("page") or 1)
        d = get_page({"Page": page, "PageSize": 100, "Category": 2,
                      "FromDate": start, "ToDate": end})
        if d is None:
            print("giving up on this window temporarily", flush=True)
            time.sleep(120)
            continue
        letters = d.get("Letters") or []
        rows = []
        for L in letters:
            title = str(L.get("Title") or "")
            if "گروه الف" not in title:
                continue
            rows.append({
                "tracing_no": L.get("TracingNo"),
                "symbol": L.get("Symbol"),
                "company": L.get("CompanyName"),
                "title": title.replace("\n", " ").strip(),
                "published": (L.get("PublishDateTime") or "").strip(),
                "letter_code": L.get("LetterCode"),
                "url": "https://codal.ir" + (L.get("Url") or ""),
            })
        if rows:
            append(rows)
        prog["events"] = int(prog.get("events") or 0) + len(rows)
        total = int(d.get("Total") or 0)
        # collect ALL pages: the API returns newest-first and Total is the
        # count for the whole window, so stopping at page*100>=Total loses the
        # older letters of the window
        print(f"win {start}..{end} page {page}: got {len(letters)} letters, "
              f"{len(rows)} type-A, total so far {prog['events']}", flush=True)
        if not letters or page * 100 >= total or page >= 200:
            prog["window_idx"] += 1
            prog["page"] = 1
        else:
            prog["page"] = page + 1
        save_progress(prog)
        time.sleep(5)
    print("DONE", prog, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
