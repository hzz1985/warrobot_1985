"""Robust parser for the daily «گواهی‌های سپرده کالایی» post.

A real post (pasted from the channel, newlines lost) looks like:

    📦 گواهی‌های سپرده کالایی🕐 2026/11/14 - 15:07🥇 شمش طلا:
    📄 گواهی شمش طلا (هر گرم): 32,865,300 تومان🌍 انس جهانی طلا: $3,883.43
    📉 حباب شمش طلا: %2.8-⚖️ قیمت بدون حباب: 33,823,232 تومان➖➖➖
    🥈 نقره:📄 گواهی نقره بورس: 434,500 تومان🌍 انس جهانی نقره: $48.74 ...
    🟠 مس کاتد:📄 گواهی مس بورس (کیلو): 3,210,900 تومان
    🌍 مس جهانی (دلار/پوند): $6.09📉 حباب ...: %2.9- ...

Every field starts with a known emoji, so they are used as line separators.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FIELD_EMOJI = ["📄", "🌍", "💲", "📉", "⚖️", "🏷", "📊", "⚠️", "🪙", "📈"]
HEAD_EMOJI = ["🥇", "🥈", "🟠", "🟡", "🌸", "🛢", "🌰", "🔵", "🟤", "⚪", "⬛", "🛻", "🥉", "🔴", "🟢"]
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+")
SEP_RE = re.compile(r"[➖─—–]{2,}")


def num(s: str) -> float | None:
    if not s:
        return None
    s = s.replace("٬", "").replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def sign_of(raw: str, value: float | None) -> float | None:
    """The channel puts the sign at the END of the number: '%2.9-' = -2.9."""
    if value is None:
        return None
    r = raw.strip()
    if r.endswith("-"):
        return -abs(value)
    if r.endswith("+"):
        return abs(value)
    if r.startswith("-"):
        return -abs(value)
    return value


def split_fields(text: str) -> str:
    t = text
    for e in FIELD_EMOJI + HEAD_EMOJI + ["➖"]:
        t = t.replace(e, "\n" + e)
    return t


def parse_post(text: str) -> dict:
    raw = text.replace("\u200c", " ").replace("٬", ",")
    head = re.search(r"(\d{4}/\d{2}/\d{2})\s*-\s*(\d{2}:\d{2})", raw)
    result = {"date": head.group(1) if head else "",
              "time": head.group(2) if head else "",
              "commodities": {}}

    line_text = split_fields(raw)
    sections: list[list[str]] = []
    cur: list[str] = []
    for line in line_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if SEP_RE.fullmatch(line) or line.startswith("➖"):
            if cur:
                sections.append(cur)
                cur = []
            continue
        cur.append(line)
    if cur:
        sections.append(cur)

    for sec in sections:
        name = ""
        items: dict = {}
        for line in sec:
            stripped = EMOJI_RE.sub("", line).strip()
            core = re.sub(r"^[^\w\u0600-\u06FF]+", "", stripped)
            if ":" not in core and not name:
                cand = core.strip(" :،")
                if cand and len(cand) > 1 and not cand.startswith("گواهی های"):
                    name = cand
                continue
            if not name:
                cand = core.split(":")[0].strip()
                if cand and len(cand) > 1:
                    name = cand
            low = core
            if "گواهی" in low and "تومان" in low:
                m = re.search(r":\s*([\d,\.]+)\s*تومان", low)
                if m:
                    items["cert_price_toman"] = num(m.group(1))
            if "$" in low:
                m = re.search(r"\$\s*([\d,\.]+)", low)
                if m:
                    items["global_usd"] = num(m.group(1))
            if "حباب" in low and "%" in low:
                m = re.search(r"%\s*([\d\.]+)\s*([-+]?)", low)
                if m:
                    v = sign_of(m.group(2), num(m.group(1)))
                    if "نسبت" in low:
                        items["premium_vs_physical_pct"] = v
                    elif "cert_price_toman" in items or "premium_pct" not in items:
                        items["premium_pct"] = v
            if "قیمت بدون حباب" in low:
                m = re.search(r":\s*([\d,\.]+)", low)
                if m:
                    items["nobubble_toman"] = num(m.group(1))
            if "نسبت" in low:
                m = re.search(r"٪\s*([\d\.]+)", low)
                if m:
                    items["ratio_pct"] = num(m.group(1))
            if "وضعیت قیمت" in low:
                m = re.search(r"وضعیت قیمت:\s*(\S+)", low)
                if m:
                    items["price_status"] = m.group(1)
            if "مالیات" in low and "نکته" in low:
                items["vat_note"] = True
            if "قیمت" in low and "$" in low and "وضعیت" not in low:
                m = re.search(r"\$\s*([\d,\.]+)", low)
                if m:
                    items.setdefault("price_usd", num(m.group(1)))
        # header fix: the first block starts with the channel title
        if name.startswith("گواهی") and "سپرده کالایی" in name:
            name = "شمش طلا"
        if name and items:
            result["commodities"][name] = items
    return result


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("post.txt")
    data = parse_post(src.read_text(encoding="utf-8"))
    dest = Path("parsed_post.json")
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{data['date']} {data['time']} · commodities: {len(data['commodities'])}")
    for k, v in data["commodities"].items():
        print(f"  {k:28s} price={v.get('cert_price_toman')!s:>12} "
              f"usd={v.get('global_usd')!s:>9} premium={v.get('premium_pct')!s:>6} "
              f"{v.get('price_status','')}")
    print("saved:", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
