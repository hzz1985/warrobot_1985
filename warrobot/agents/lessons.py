"""Lessons: the bot's own case-based memory ("training" without fine-tuning).

Every resolved incident is stored as one structured lesson:

    {"id": "...", "symptom": "...", "cause": "...", "fix": "...",
     "verify": "...", "tags": ["login", "captcha"] + [
    {"id": "generic-attr-error", "tags": ["attributeerror", "none", "list"],
     "symptom": "AttributeError: 'list' object has no attribute / NoneType object has no attribute",
     "cause": "تابع در حالت خطا مقدار غیرمنتظره برگردانده (None یا list)",
     "fix": "در caller از `x = f() or {}` و isinstance استفاده شود",
     "verify": "اجرای دوبارهٔ اسکریپت بدون traceback"},
    {"id": "generic-file-missing", "tags": ["filenotfound", "no such file"],
     "symptom": "FileNotFoundError: No such file or directory",
     "cause": "فایل state مورد انتظار ساخته نشده یا مسیر تغییر کرده",
     "fix": "makedirs + تولید فایل در زمان‌بند قبل از مصرف‌کننده",
     "verify": "وجود فایل در مسیر مورد انتظار"},
    {"id": "generic-keyerror", "tags": ["keyerror"],
     "symptom": "KeyError",
     "cause": "کلید جدید/تغییر یافته در داده یا قالب",
     "fix": "دسترسی با .get و مقدار پیش‌فرض",
     "verify": "اجرای دوباره بدون KeyError"},
], "at": "..."}

Agents call `find(text)` before escalating. If a known symptom matches, the agent
applies the recorded fix automatically (or reports it as a known issue with the
fix), which is what keeps the architect out of the daily loop.

This is deliberately retrieval-based, not a fine-tuned model: on 4 vCPU and 8 GB
RAM a 1.5B model cannot reason well in Persian, but keyword/lesson matching is
exact, instant, free and auditable.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / "state"
FILE = STATE / "lessons.jsonl"

SEED = [
    {"id": "login-captcha-click", "tags": ["login", "captcha", "ورود", "timeout"],
     "symptom": "page.click: Timeout 30000ms exceeded waiting for #captcha",
     "cause": "فرم ورود دیگر کلیک‌پذیر نیست؛ offsetParent هم مخفی بودن را نشان نمی‌دهد",
     "fix": "مقداردهی فیلدها با native setter + تشخیص لاگین بر اساس URL و getBoundingClientRect",
     "verify": "node pw/balance.js → account_balance > 0"},
    {"id": "pnl-zero-blend", "tags": ["pnl", "avg", "سود"],
     "symptom": "price/avg-1 با pnl_pct یکی نیست",
     "cause": "میانگین بدون کارمزد نمایش داده می‌شد ولی درصد از پنل (با کارمزد) می‌آمد",
     "fix": "avg_cost = avg_cost_fee؛ avg_cost_raw برای مرجع",
     "verify": "pnl.py → |calc - pnl_pct| < 0.05"},
    {"id": "stale-tsetmc-close", "tags": ["price", "stale", "قیمت", "tsetmc"],
     "symptom": "قیمت سفارش با قیمت پنل فرق دارد / قیمت ۶ هفته کهنه",
     "cause": "closingPriceDaily برای برخی نمادها قدیمی است",
     "fix": "قیمت کهنه (>۵ روز) رد شود؛ قیمت پنل کارگزار مبنای سفارش باشد",
     "verify": "order_candidates --dry → warnings[].stale_close"},
    {"id": "orphan-chrome", "tags": ["chrome", "profile", "lock", "کروم"],
     "symptom": "Opening in existing browser session / free balance 0",
     "cause": "اسکریپت Playwright در مسیر خطا مرورگر را نمی‌بندد؛ پروفایل قفل می‌ماند",
     "fix": "pw_reset قبل/بعد هر اجرا + پاکسازی در pw_lock + job 08:44",
     "verify": "pw_reset.py → killed_chrome=0"},
    {"id": "open-order-blocked", "tags": ["order", "open", "سفارش"],
     "symptom": "order job ran but submitted=0 with no error",
     "cause": "سفارش روز قبل باز مانده و نگهبان سفارش باز آن را دید",
     "fix": "preopen_cleanup ساعت 08:43 سفارش‌های جلسهٔ قبل را لغو می‌کند",
     "verify": "state/preopen_cleanup.json → stale=0"},
    {"id": "false-ready-email", "tags": ["email", "ready", "ایمیل"],
     "symptom": "ایمیل «ربات سالم است» ولی سفارشی ثبت نشده",
     "cause": "ایمیل صبح فقط ورود/موجودی را چک می‌کرد، نه نتیجهٔ سفارش",
     "fix": "متن ایمیل صبح «آمادگی پیش از بازار (سفارش هنوز اجرا نشده)» + order_truth ساعت ۰۹:۰۲",
     "verify": "state/order_truth.json → verdict"},
    {"id": "blended-tp-miss", "tags": ["tp", "take profit", "حد سود"],
     "symptom": "لات اول +۲۵٪ شده ولی ربات نفروخته چون میانگین کل ۸٪ است",
     "cause": "TP روی میانگین وزنی محاسبه می‌شد، نه روی هر خرید",
     "fix": "lot_exits.py: هر لات با هزینهٔ خودش سنجیده می‌شود + منع تکمیل خرید نزدیک TP",
     "verify": "lot_exits.py --dry → due[]"},
    {"id": "webapp-stale", "tags": ["webapp", "ui", "وب‌اپ"],
     "symptom": "وب‌اپ به‌روز نیست / عدد قدیمی",
     "cause": "pnl.py خواندن خالی پنل را «نگه‌داشتن اسنپ‌شات قبلی» کرد و کسی متوجه نشد",
     "fix": "agents/webapp_watch.py تازگی و سازگاری را چک و خودکار تازه می‌کند + رفرش ۶۰ ثانیهٔ صفحه",
     "verify": "state/agents/a3_webapp.json → ok=true"},
    {"id": "iran-telegram-block", "tags": ["telegram", "کانال", "rss"],
     "symptom": "t.me فیلتر / آینه‌ها SSL error",
     "cause": "فیلترینگ + interception",
     "fix": "پل RSS: rsshub.rssforever.com/telegram/channel/<name>",
     "verify": "research/tg_channel_collector.py → fetched>0"},
    {"id": "codal-date-filter", "tags": ["codal", "date", "کدال"],
     "symptom": "همهٔ رویدادها پایان فصل خوردند",
     "cause": "FromDate/ToDate کدال روی پایان دورهٔ مالی فیلتر می‌کند نه انتشار",
     "fix": "فچ بر اساس نماد (بدون فیلتر تاریخ) + همهٔ صفحات",
     "verify": "state/codal_typeA_bysymbol.csv"},
]


def _norm(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"[\u200c\u200f\u200e]", "", t)
    return t


def load() -> list[dict]:
    rows = []
    if FILE.exists():
        for line in FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def ensure_seed() -> int:
    rows = load()
    have = {r.get("id") for r in rows}
    added = 0
    with FILE.open("a", encoding="utf-8") as fh:
        for s in SEED:
            if s["id"] in have:
                continue
            s = dict(s)
            s["at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            s["source"] = "seed"
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
            added += 1
    return added


def record(symptom: str, cause: str, fix: str, verify: str = "",
           tags: list[str] | None = None, source: str = "architect") -> dict:
    ensure_seed()
    rec = {"id": f"lesson-{datetime.now().strftime('%Y%m%d%H%M%S')}",
           "symptom": symptom, "cause": cause, "fix": fix, "verify": verify,
           "tags": tags or [], "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "source": source}
    with FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def find(text: str, limit: int = 3) -> list[dict]:
    """Best keyword matches for an error text (no model involved)."""
    ensure_seed()
    words = {w for w in re.findall(r"[\w\u0600-\u06FF]{3,}", _norm(text))}
    scored = []
    for r in load():
        hay = _norm(" ".join([r.get("symptom", ""), r.get("cause", ""),
                              " ".join(r.get("tags") or [])]))
        hits = sum(1 for w in words if w in hay)
        if hits:
            scored.append((hits, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:limit]]


def main() -> int:
    added = ensure_seed()
    print(json.dumps({"seeded": added, "total": len(load())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
