"""Jalali holiday support for the trading calendar.

Only *fixed solar* holidays are listed here — they are certain. Lunar (religious)
holidays shift every year and a wrong entry would silently skip a trading day,
which is worse than trying on a closed day (the order simply does not fill).
Add those dates from the official exchange calendar when announced:

    python -m market_cal --add 1405-09-12     # marks that Jalali date as holiday
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
HOLIDAYS = BASE / "state" / "holidays.json"

FIXED_1405 = [
    "1405-01-01", "1405-01-02", "1405-01-03", "1405-01-04",   # نوروز
    "1405-01-12", "1405-01-13",                                # ۱۲ و ۱۳ فروردین
    "1405-03-14", "1405-03-15",                                # ۱۴ و ۱۵ خرداد
    "1405-11-22",                                              # ۲۲ بهمن
    "1405-12-29",                                              # ۲۹ اسفند
]


def _load() -> dict:
    try:
        return json.loads(HOLIDAYS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    HOLIDAYS.parent.mkdir(parents=True, exist_ok=True)
    HOLIDAYS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def ensure_seed() -> int:
    data = _load()
    y = "1405"
    lst = data.setdefault(y, [])
    added = 0
    for d in FIXED_1405:
        if d not in lst:
            lst.append(d)
            added += 1
    lst.sort()
    data.setdefault("note", "تعطیلات ثابت شمسی؛ تعطیلات قمری باید از تقویم رسمی افزوده شود")
    _save(data)
    return added


def jalali_today(when: datetime | None = None) -> str:
    try:
        import jdatetime
        g = (when or datetime.now()).date()
        j = jdatetime.date.fromgregorian(date=g)
        return f"{j.year:04d}-{j.month:02d}-{j.day:02d}"
    except Exception:
        return ""


def is_holiday(when: datetime | None = None) -> bool:
    j = jalali_today(when)
    if not j:
        return False
    data = _load()
    return any(j in (data.get(y) or []) for y in data if y.isdigit())


def sessions_present() -> set:
    """Jalali dates that really have a trading session in the local daily data."""
    out = set()
    daily = BASE / "TeseRobot" / "data" / "daily"
    try:
        for fp in sorted(daily.glob("*_daily.csv"))[:40]:
            try:
                with fp.open(encoding="utf-8-sig") as fh:
                    next(fh, None)
                    for line in fh:
                        d = line.split(",", 1)[0].strip()
                        if len(d) == 10 and d[4] == "-":
                            out.add(d)
            except Exception:
                continue
    except Exception:
        pass
    return out


def remove(date_str: str) -> dict:
    data = _load()
    year = date_str.split("-")[0]
    lst = data.get(year) or []
    if date_str in lst:
        lst.remove(date_str)
        data[year] = lst
        _save(data)
        return {"removed": date_str, "year": year}
    return {"removed": None, "reason": "not present"}


def add_checked(date_str: str) -> dict:
    """Add a holiday only when no trading session exists for that date."""
    today = jalali_today()
    if today and date_str > today:
        # announced holidays are often in the future; we cannot verify those
        res = add(date_str)
        return {"ok": True, "warning": "future date — unverifiable against sessions", **res}
    if date_str in sessions_present():
        return {"ok": False, "date": date_str,
                "reason": "این تاریخ دادهٔ معاملاتی دارد — تعطیل اعلام نمی‌شود"}
    res = add(date_str)
    return {"ok": True, **res}


def add(date_str: str) -> dict:
    data = _load()
    year = date_str.split("-")[0]
    lst = data.setdefault(year, [])
    if date_str not in lst:
        lst.append(date_str)
        lst.sort()
    _save(data)
    return {"added": date_str, "year": year, "count": len(lst)}


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--remove" in args:
        i = args.index("--remove")
        if i + 1 < len(args):
            print(json.dumps(remove(args[i + 1]), ensure_ascii=False))
            raise SystemExit(0)
    if "--add" in args:
        i = args.index("--add")
        if i + 1 < len(args):
            print(json.dumps(add_checked(args[i + 1]), ensure_ascii=False))
            raise SystemExit(0)
    added = ensure_seed()
    print(json.dumps({"seeded": added, "today": jalali_today(),
                      "is_holiday": is_holiday()}, ensure_ascii=False))
