"""Extract the commodity price tables from the IME homepage (real data, no Telegram).

Shows which commodities are available from the official source and gives a first
look at the structure needed for a commodity-price database.
"""
from __future__ import annotations

import re
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/157.0"}
URL = "https://ime.co.ir/"


def strip(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t).strip()


def main() -> int:
    html = urllib.request.urlopen(urllib.request.Request(URL, headers=H), timeout=30
                                  ).read().decode("utf-8", "replace")
    tables = re.findall(r"<table.*?</table>", html, re.S)
    print(f"tables found: {len(tables)}")
    shown = 0
    for ti, t in enumerate(tables):
        rows = re.findall(r"<tr.*?</tr>", t, re.S)
        parsed = [[strip(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)] for r in rows]
        parsed = [p for p in parsed if p]
        if len(parsed) < 3:
            continue
        # keep tables that mention a commodity keyword
        flat = " ".join(" ".join(p) for p in parsed[:12])
        if not any(k in flat for k in ("مس", "روی", "فولاد", "میلگرد", "کنسانتره", "آلومینیوم")):
            continue
        print(f"\n--- table {ti}: {len(parsed)} rows ---")
        for p in parsed[:6]:
            print(" | ".join(x[:28] for x in p[:7]))
        shown += 1
        if shown >= 3:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
