"""Collector/parser for the Telegram channel «نقره لحظه» via a public RSS bridge.

Telegram itself is unreachable from this server (t.me refused, mirrors TLS-broken),
but https://rsshub.rssforever.com/telegram/channel/<name> serves the recent posts
and is reachable. Every post is an intraday snapshot of the commodity-certificate
market:

    📦 گواهی‌های سپرده کالایی   🕐 2026/11/14 - 13:51
    🥇 شمش طلا: گواهی شمش طلا (هر گرم): 32,950,130 تومان
        انس جهانی طلا: $3,883.43 · حباب شمش طلا: %2.9- · قیمت بدون حباب: ...
    🥈 نقره: ...

The collector appends one structured row per post to research/tg_noghre.jsonl
(deduplicated by the channel timestamp) so a real intraday series builds up.
"""
from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
OUT = BASE / "research" / "tg_noghre.jsonl"
FEEDS = [
    "https://rsshub.rssforever.com/telegram/channel/Noghre_lahze",
]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/157.0"}


def _num(text: str) -> float | None:
    if not text:
        return None
    t = text.replace(",", "").replace("٬", "").replace("،", "")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    return float(m.group(0)) if m else None


def parse_post(text: str) -> dict:
    """Turn one snapshot message into structured numbers."""
    clean = re.sub(r"\s+", " ", html.unescape(text)).strip()
    row = {"raw": clean}
    m = re.search(r"(\d{4}/\d{2}/\d{2})\s*-\s*(\d{2}:\d{2})", clean)
    if m:
        row["date"] = m.group(1)
        row["time"] = m.group(2)

    def grab(pattern: str) -> float | None:
        mm = re.search(pattern, clean)
        return _num(mm.group(1)) if mm else None

    row["gold_gram_toman"] = grab(r"گواهی شمش طلا \(هر گرم\):\s*([\d,]+)")
    row["gold_ounce_usd"] = grab(r"انس جهانی طلا:\s*\$?([\d,.]+)")
    row["gold_bubble_pct"] = grab(r"حباب شمش طلا:\s*%?\s*(-?[\d.]+)")
    row["gold_nobubble_toman"] = grab(r"قیمت بدون حباب:\s*([\d,]+)")
    row["silver_gram_toman"] = grab(r"گواهی نقره[^:]*:\s*([\d,]+)")
    row["silver_ounce_usd"] = grab(r"انس جهانی نقره:\s*\$?([\d,.]+)")
    row["silver_bubble_pct"] = grab(r"حباب نقره:\s*%?\s*(-?[\d.]+)")
    # direction markers used by the channel
    row["gold_dir"] = ("down" if "📉" in clean else
                       "up" if "📈" in clean else "flat")
    return row


def fetch(feed: str) -> list[dict]:
    req = urllib.request.Request(feed, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    items = re.findall(r"<item>(.*?)</item>", raw, re.S)
    out = []
    for it in items:
        desc = re.findall(r"<description>(.*?)</description>", it, re.S)
        pub = re.findall(r"<pubDate>(.*?)</pubDate>", it, re.S)
        link = re.findall(r"<link>(.*?)</link>", it, re.S)
        if not desc:
            continue
        row = parse_post(desc[0])
        row["pub"] = (pub[0] if pub else "").strip()
        row["link"] = (link[0] if link else "").strip()
        out.append(row)
    return out


def existing_keys() -> set:
    keys = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                keys.add((d.get("date"), d.get("time")))
            except Exception:
                pass
    return keys


COMMODITY_LOG = BASE / "research" / "tg_commodity_daily.jsonl"


def parse_commodities(raw_html: str) -> dict:
    """Run the daily-post parser and keep posts that carry several commodities."""
    try:
        sys.path.insert(0, str(BASE / "research"))
        import tg_post_parser as pp
        plain = html.unescape(raw_html)
        plain = re.sub(r"<br\s*/?>", "", plain)
        plain = re.sub(r"<[^>]+>", "", plain)
        data = pp.parse_post(plain)
    except Exception:
        return {}
    return data if len(data.get("commodities") or {}) >= 3 else {}


def main() -> int:
    rows = []
    for feed in FEEDS:
        try:
            rows.extend(fetch(feed))
        except Exception as exc:
            print("feed error:", str(exc)[:100])
    seen = existing_keys()
    new = [r for r in rows if (r.get("date"), r.get("time")) not in seen]
    if new:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("a", encoding="utf-8") as fh:
            for r in new:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    commodity_keys = set()
    if COMMODITY_LOG.exists():
        for line in COMMODITY_LOG.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                commodity_keys.add((d.get("date"), d.get("time")))
            except Exception:
                pass
    if new:
        with COMMODITY_LOG.open("a", encoding="utf-8") as fh:
            for r in new:
                parsed = parse_commodities(r.get("raw", ""))
                if parsed and (parsed.get("date"), parsed.get("time")) not in commodity_keys:
                    commodity_keys.add((parsed.get("date"), parsed.get("time")))
                    parsed["_source"] = "rss_bridge"
                    fh.write(json.dumps(parsed, ensure_ascii=False) + "\n")
    print(json.dumps({"fetched": len(rows), "new": len(new),
                      "total": len(seen) + len(new),
                      "sample": new[:2]}, ensure_ascii=False)[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
