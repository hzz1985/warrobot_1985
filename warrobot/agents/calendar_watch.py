"""Calendar maintenance (a8): confirm holidays from real sessions, never guess.

The operator accepted the rule: lunar holidays must come from the official
calendar, not from a model. This module makes that safe:

  1. `--add <jalali-date>` refuses to mark a day as a holiday when the local
     trading data proves a session existed that day (the expensive mistake).
  2. `--suspects` looks at the newest session in the local daily files after the
     nightly download: if a Sat..Wed day produced no session, it records a
     "suspected holiday" for the operator instead of adding it automatically.
  3. `--confirm <jalali-date>` adds a suspect after human confirmation.

    python -m market_holidays --add 1405-09-12
    python -m agents.calendar_watch
    python -m agents.calendar_watch --confirm 1405-09-12
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import market_holidays as mh
import market_cal


def _sessions() -> set[str]:
    """Every Jalali date that actually has a trading session in the local data."""
    dates: set[str] = set()
    daily = BASE.parent / "TeseRobot" / "data" / "daily"
    try:
        files = sorted(daily.glob("*_daily.csv"))
    except Exception:
        return dates
    for fp in files[:40]:                      # 40 symbols are enough for a calendar
        try:
            with fp.open(encoding="utf-8-sig") as fh:
                next(fh, None)
                for line in fh:
                    d = line.split(",", 1)[0].strip()
                    if len(d) == 10 and d[4] == "-":
                        dates.add(d)
        except Exception:
            continue
    return dates


def add_checked(jalali_date: str) -> dict:
    sessions = _sessions()
    if jalali_date in sessions:
        return {"ok": False, "added": None, "date": jalali_date,
                "reason": "این تاریخ در دادهٔ معاملاتی وجود دارد — تعطیل نیست"}
    res = mh.add(jalali_date)
    return {"ok": True, **res}


def suspects() -> dict:
    """A Sat..Wed day with no session in the newest data = suspected holiday."""
    sessions = _sessions()
    if not sessions:
        return {"ok": False, "reason": "no local session data"}
    latest = max(sessions)
    today = mh.jalali_today()
    import json
    out_file = BASE / "state" / "calendar_suspects.json"
    suspects_list = []
    try:
        from jdatetime import date as jdate
        y, m, d = [int(x) for x in latest.split("-")]
        # walk backwards from today to the newest session, collecting missing Sat..Wed days
        import jdatetime
        cur = jdatetime.date(*[int(x) for x in today.split("-")])
        last = jdatetime.date(y, m, d)
        step = 0
        while cur > last and step < 10:
            g = cur.togregorian()
            if g.weekday() in market_cal.TRADING_WEEKDAYS:
                suspects_list.append(cur.strftime("%Y-%m-%d"))
            cur = cur - jdatetime.timedelta(days=1)
            step += 1
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:100]}
    payload = {"at": mh.jalali_today(), "latest_session": latest,
               "suspects": suspects_list,
               "note": "این روزها جلسهٔ معاملاتی نداشتند؛ پس از تأیید تقویم رسمی با --confirm اضافه شوند"}
    out_file.parent.mkdir(parents=True, exist_ok=True)
    import json as _j
    out_file.write_text(_j.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, **payload}


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--confirm":
        print(add_checked(args[1]))
        return 0
    print(suspects())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
