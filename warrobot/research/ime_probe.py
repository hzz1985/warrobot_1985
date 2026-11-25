"""Probe the IME (بورس کالا) website for machine-readable commodity prices.

The official site is reachable from this server (200), while Telegram is not.
Goal: find an endpoint/page that carries steel / copper / zinc / iron-ore prices
so the channel's information can be rebuilt from the primary source.
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


def fetch(url: str) -> str:
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30
                                      ).read().decode("utf-8", "replace")
    except Exception as exc:
        print(f"  ERR {url[:60]}: {str(exc)[:60]}")
        return ""


PAGES = [
    "https://ime.co.ir/PriceBoard",
    "https://ime.co.ir/",
    "https://ime.co.ir/MarketWatch",
]
KEYWORDS = ["فولاد", "مس", "روی", "سنگ آهن", "میلگرد", "کنسانتره", "گندله",
            "آهن اسفنجی", "آلومینیوم", "کک", "متانول", "اوره"]

for url in PAGES:
    html = fetch(url)
    if not html:
        continue
    print(f"\n=== {url} ({len(html)} chars) ===")
    hits = {k: html.count(k) for k in KEYWORDS if html.count(k)}
    print("keyword hits:", hits)
    apis = set(re.findall(r"""['"](/[A-Za-z0-9_\-/]*(?:api|Api|Json|json|Data|data)[A-Za-z0-9_\-/]*)['"]""", html))
    print("api-ish paths:", list(apis)[:12])
    tables = html.count("<table")
    rows = html.count("<tr")
    print(f"tables={tables} rows={rows}")
