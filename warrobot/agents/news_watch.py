"""A5 — economic news watch (safety flag only, never a trading signal).

Scope agreed with the operator:
  * it does NOT change candidate selection, TP, SL or sizing;
  * it looks at public headlines and only raises a FLAG when a held/candidate
    symbol or a hard-risk phrase appears;
  * Persian output is produced from templates (the 1.5B local model cannot write
    reliable Persian); the local model may add ONE English summary off-hours.

Sources reachable from this server: irna.ir, shana.ir, seo.ir, fipiran.com.
telegram/codal are blocked/rate-limited (documented elsewhere).
"""
from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
OUT = STATE / "agents" / "a5_news.json"

FEEDS = [
    "https://www.irna.ir/rss",
    "https://www.shana.ir/fa/rss",
    "https://www.seo.ir/rss",
]
RISK_WORDS = ["توقف نماد", "افزایش سرمایه", "تعدیل", "تحریم", "نرخ خوراک",
              "نرخ گاز", "مالیات", "بازگشایی نماد", "سود نقدی", "مجمع"]
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/157.0"}


def _titles(url: str) -> list[str]:
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=H),
                                     timeout=25).read().decode("utf-8", "replace")
    except Exception:
        return []
    items = re.findall(r"<item.*?</item>", raw, re.S)
    out = []
    for it in items[:30]:
        t = re.findall(r"<title>(.*?)</title>", it, re.S)
        if t:
            out.append(html.unescape(re.sub(r"<[^>]+>", " ", t[0])).strip())
    if not out:
        out = [html.unescape(re.sub(r"<[^>]+>", " ", x)).strip()
               for x in re.findall(r"<h[12][^>]*>(.*?)</h[12]>", raw, re.S)[:30]]
    return [x for x in out if 10 < len(x) < 200]


def _held_symbols() -> list[str]:
    try:
        d = json.loads((STATE / "pnl.json").read_text(encoding="utf-8"))
        return [str(p["symbol"]) for p in d.get("positions", [])
                if p.get("symbol") not in ("افران",)]
    except Exception:
        return []


def run() -> dict:
    heads: list[dict] = []
    per_feed = {}
    for f in FEEDS:
        ts = _titles(f)
        per_feed[f] = len(ts)
        for t in ts:
            heads.append({"title": t, "source": f})
    symbols = _held_symbols() + ["آواپارس"]
    flags = []
    seen = set()
    for h in heads:
        t = h["title"]
        if t in seen:
            continue
        seen.add(t)
        hit_sym = next((s for s in symbols if s and s in t), None)
        hit_risk = next((w for w in RISK_WORDS if w in t), None)
        if hit_sym or hit_risk:
            flags.append({"title": t, "symbol": hit_sym, "risk": hit_risk,
                          "source": h["source"]})

    summary = None
    if flags:
        try:
            from agents import llm
            summary = llm.ask("Summarize these market headlines in 3 short English bullets:\n"
                              + "\n".join(f"- {f['title']}" for f in flags[:8]))
        except Exception:
            summary = None

    text = "پرچم‌های خبری امروز: " + ("موردی نبود" if not flags else f"{len(flags)} مورد")
    out = {"agent": "a5_news", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "feeds": per_feed, "headlines": len(heads), "flags": flags[:15],
           "persian_summary": text, "llm_summary": summary,
           "policy": "فقط پرچم ایمنی؛ بدون تأثیر بر انتخاب سهم یا TP/SL",
           "ok": True}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False)[:700])
