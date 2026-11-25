"""Watchdog: prove that every scheduled job actually ran.

Each job writes a marker file when it finishes. This script compares the marker age
with the job's allowed staleness and emails a single report when something is late.

A "firewall" for the pipeline: no more silent gaps (the bug of 2026-10-25 was a job
that simply never ran and nobody noticed).
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P / "src"))
sys.path.insert(0, str(P.parent / "TeseRobot" / "src"))

STATE = P / "state"
LOG = P / "bot.log"

JOBS = [
    ("دانلود شبانه", "last_download.txt", 1, "۲۰:۳۰"),
    ("ردیابی ۱۴ روزه", "last_tracker.txt", 1, "۲۱:۲۵"),
    ("نسبت پول درشت", "last_big_small.txt", 1, "۲۱:۳۰"),
    ("اسکن شبانه و ایمیل", "last_scan.txt", 1, "۲۱:۲۰"),
    ("تست خودکار سفارش", "last_selftest.txt", 1, "۰۸:۲۵"),
    ("یادگیری وزن‌ها", "last_learn.txt", 15, "۲ هفته یک‌بار"),
]


def marker_date(name: str):
    f = STATE / name
    if not f.exists():
        return None
    try:
        raw = f.read_text(encoding="utf-8-sig").strip()[:10]
        return date.fromisoformat(raw)
    except Exception:
        return None


def main() -> int:
    today = date.today()
    rows, bad = [], []
    for label, marker, max_age, when in JOBS:
        d = marker_date(marker)
        age = None if d is None else (today - d).days
        ok = d is not None and age <= max_age
        rows.append({"job": label, "marker": marker, "last": str(d) if d else "-",
                     "age_days": age, "allowed": max_age, "ok": ok, "schedule": when})
        if not ok:
            bad.append(f"{label} (آخرین: {d or 'هیچ'}, مجاز: {max_age} روز)")

    # heartbeat: the daemon must have written to bot.log recently
    hb_ok = LOG.exists() and (datetime.now() - datetime.fromtimestamp(LOG.stat().st_mtime)).total_seconds() < 7200
    rows.append({"job": "heartbeat ربات", "marker": "bot.log", "last": "-", "age_days": None,
                 "allowed": 0.08, "ok": hb_ok, "schedule": "هر ۳۰ دقیقه"})
    if not hb_ok:
        bad.append("heartbeat ربات (لاگ در ۲ ساعت گذشته به‌روز نشده)")

    lines = ["گزارش فایروال — بررسی اجرای همهٔ کارهای زمان‌بندی‌شده", ""]
    for r in rows:
        mark = "✔" if r["ok"] else "✘"
        lines.append(f"{mark} {r['job']:22s} آخرین={r['last']:10s} سن={r['age_days']} روز · زمان‌بندی {r['schedule']}")
    lines += ["", f"نتیجه: {len(rows)-len(bad)} از {len(rows)} سالم"]
    if bad:
        lines += ["", "موارد معوق:"] + [f"  - {b}" for b in bad]
    report = "\n".join(lines)
    print(report)

    (STATE / "watchdog_last.json").write_text(
        json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "rows": rows,
                    "failures": bad}, ensure_ascii=False, indent=1), encoding="utf-8")

    if bad:
        try:
            from tsebot.alerts import send
            send("[WarRobot] فایروال: کار زمان‌بندی‌شده اجرا نشده", report, key="watchdog", force=True)
        except Exception as exc:
            print("email error:", str(exc)[:80])
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
