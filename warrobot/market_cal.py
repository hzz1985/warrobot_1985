"""Tehran market calendar + one-shot email helper.

The two recurring complaints this file fixes:
  * the bot tried to log in on Thursday/Friday (market closed) and sent "not
    logged in" mails before the market even opened;
  * the same alert was emailed again and again.

market_day(): Saturday..Wednesday are trading days (Iranian week), Thursday and
Friday are closed. Official holidays still need a manual switch, but the weekly
guard alone removes the bulk of the noise.

send_once(): sends at most one email per key per day.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
STATE = BASE / "state" / "emails_sent.json"

# Python weekday(): Mon=0 ... Sun=6 ; Iranian trading days: Sat(5),Sun(6),Mon(0),Tue(1),Wed(2)
TRADING_WEEKDAYS = {5, 6, 0, 1, 2}
CLOSED_WEEKDAYS = {3, 4}          # Thursday, Friday


def now_tehran() -> datetime:
    try:
        sys.path.insert(0, str(BASE / "src"))
        sys.path.insert(0, str(BASE))
        import distbot as bot
        return bot.now_tehran()
    except Exception:
        return datetime.now()


def market_day(when: datetime | None = None) -> bool:
    """Saturday..Wednesday and not an announced holiday."""
    when = when or now_tehran()
    if when.weekday() not in TRADING_WEEKDAYS:
        return False
    try:
        import market_holidays
        market_holidays.ensure_seed()
        if market_holidays.is_holiday(when):
            return False
    except Exception:
        pass
    return True


def market_day_name(when: datetime | None = None) -> str:
    when = when or now_tehran()
    names = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
    return names[when.weekday()]


def reason_closed(when: datetime | None = None) -> str:
    when = when or now_tehran()
    if market_day(when):
        return ""
    try:
        import market_holidays
        if market_holidays.is_holiday(when):
            return "تعطیل رسمی (تقویم شمسی)"
    except Exception:
        pass
    return f"بازار بسته است ({market_day_name(when)})"


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def already_sent(key: str) -> bool:
    day = now_tehran().strftime("%Y-%m-%d")
    return key in _load().get(day, {})


def mark_sent(key: str) -> None:
    day = now_tehran().strftime("%Y-%m-%d")
    data = _load()
    data.setdefault(day, {})[key] = now_tehran().strftime("%H:%M")
    for old in sorted(data)[:-14]:
        data.pop(old, None)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def send_once(key: str, subject: str, body: str, force: bool = False) -> bool:
    """Email at most once per key per day; returns True when it was sent."""
    if not force and already_sent(key):
        return False
    try:
        sys.path.insert(0, str(BASE.parent / "TeseRobot" / "src"))
        from tsebot.alerts import send as _send
        _send(subject, body, key=key, force=True)
        mark_sent(key)
        return True
    except Exception as exc:
        print("email error:", str(exc)[:100])
        return False
