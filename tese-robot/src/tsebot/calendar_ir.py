# -*- coding: utf-8 -*-
"""TSE Bot v2 — time and Jalali trading calendar.

Fixes vs v1:
  * no hard-coded "before_open" escape hatch for the sell path;
  * explicit market session model (pre-open / continuous / closing auction);
  * DST-safe Asia/Tehran via zoneinfo (never fixed +03:30 arithmetic);
  * Jalali holidays loaded from a data file and extensible, with a weekday rule
    (Thu/Fri closed) that is consistent everywhere.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import jdatetime

TEHRAN = ZoneInfo("Asia/Tehran")

# Fixed Jalali holidays for 1404-1406 (national + major religious, approximate
# for lunar occasions). Kept in a file so it can be updated without code change.
_DEFAULT_HOLIDAYS = [
    "1404-01-01", "1404-01-02", "1404-01-03", "1404-01-04", "1404-01-12", "1404-01-13",
    "1404-03-14", "1404-03-15", "1404-04-05", "1404-05-14", "1404-06-22", "1404-06-30",
    "1404-12-29",
    "1405-01-01", "1405-01-02", "1405-01-03", "1405-01-04", "1405-01-12", "1405-01-13",
    "1405-03-14", "1405-03-15", "1405-04-25", "1405-06-04", "1405-06-12", "1405-06-20",
    "1405-12-29",
]


def now_tehran() -> datetime:
    """Wall clock in Tehran, corrected by a configurable drift.

    The host clock was 30 minutes behind UTC (2026-10-21), which made the bot miss
    the 09:00-09:05 سرخطی window entirely. `system.clock_offset_sec` lets us correct
    that without administrator rights to change the OS clock.
    """
    import json as _json
    from pathlib import Path as _P
    offset = 0.0
    try:
        cfgp = _P(__file__).resolve().parents[2] / "config" / "config.json"
        offset = float(_json.loads(cfgp.read_text(encoding="utf-8")).get("system", {}).get("clock_offset_sec", 0) or 0)
    except Exception:
        offset = 0.0
    return datetime.now(TEHRAN) + timedelta(seconds=offset)


def to_jalali(d: date | datetime) -> jdatetime.date:
    if isinstance(d, datetime):
        d = d.date()
    return jdatetime.date.fromgregorian(date=d)


def from_jalali(j: str | jdatetime.date) -> date:
    if isinstance(j, jdatetime.date):
        return j.togregorian()
    y, m, d = (int(x) for x in j.replace("/", "-").split("-"))
    return jdatetime.date(y, m, d).togregorian()


def jalali_str(d: date | datetime) -> str:
    return to_jalali(d).strftime("%Y-%m-%d")


class TradingCalendar:
    def __init__(self, holidays: Optional[Iterable[str]] = None, holidays_path: Path | None = None):
        hol = set(holidays or [])
        if not hol:
            hol = set(_DEFAULT_HOLIDAYS)
        if holidays_path and Path(holidays_path).exists():
            extra = json.loads(Path(holidays_path).read_text(encoding="utf-8"))
            if isinstance(extra, list):
                hol |= set(extra)
        self.holidays = hol

    # ---------------------------------------------------------------- day level
    def is_trading_day(self, d: date | datetime) -> tuple[bool, str]:
        if isinstance(d, datetime):
            d = d.date()
        jd = to_jalali(d)
        js = jd.strftime("%Y-%m-%d")
        if js in self.holidays:
            return False, "holiday"
        # jdatetime.weekday(): 0=Saturday ... 5=Thursday, 6=Friday
        wd = jd.weekday()
        if wd == 6:
            return False, "friday"
        if wd == 5:
            return False, "thursday"
        return True, "open"

    def next_trading_day(self, d: date | datetime) -> date:
        cur = d.date() if isinstance(d, datetime) else d
        for _ in range(30):
            cur += timedelta(days=1)
            if self.is_trading_day(cur)[0]:
                return cur
        raise RuntimeError("no trading day found in 30 days")

    def prev_trading_day(self, d: date | datetime) -> date:
        cur = d.date() if isinstance(d, datetime) else d
        for _ in range(30):
            cur -= timedelta(days=1)
            if self.is_trading_day(cur)[0]:
                return cur
        raise RuntimeError("no trading day found in 30 days")

    def last_trading_day(self, d: date | datetime) -> date:
        cur = d.date() if isinstance(d, datetime) else d
        if self.is_trading_day(cur)[0]:
            return cur
        return self.prev_trading_day(cur)

    def trading_days_between(self, d0: date, d1: date) -> int:
        """Number of trading days in (d0, d1]. Used for days_held."""
        if d1 < d0:
            return 0
        n, cur = 0, d0
        while cur < d1:
            cur += timedelta(days=1)
            if self.is_trading_day(cur)[0]:
                n += 1
        return n

    # ------------------------------------------------------------- intraday level
    def session(self, mkt: dict, ts: Optional[datetime] = None) -> str:
        """Return one of: closed_day, pre_open, continuous, closing_auction, after_close."""
        ts = ts or now_tehran()
        if not self.is_trading_day(ts)[0]:
            return "closed_day"
        t = ts.time()
        pre = _t(mkt.get("pre_open_start", "08:30"))
        op = _t(mkt.get("open", "09:00"))
        auc = _t(mkt.get("close_auction_start", "12:00"))
        cl = _t(mkt.get("close", "12:30"))
        if t < pre:
            return "before_pre_open"
        if t < op:
            return "pre_open"
        if t < auc:
            return "continuous"
        if t < cl:
            return "closing_auction"
        return "after_close"

    def is_open_for_orders(self, mkt: dict, ts: Optional[datetime] = None,
                           allow_pre_open: bool = False) -> tuple[bool, str]:
        s = self.session(mkt, ts)
        if s in ("continuous", "closing_auction"):
            return True, s
        if allow_pre_open and s == "pre_open":
            return True, s
        return False, s

    def is_open_for_sell(self, mkt: dict, ts: Optional[datetime] = None,
                         window: Optional[list] = None) -> tuple[bool, str]:
        """Selling is allowed ONLY while the market can match orders.

        Deliberately no 'before_open' exception: v1 sold at 03:30 because of it.
        """
        ts = ts or now_tehran()
        s = self.session(mkt, ts)
        if s in ("continuous", "closing_auction"):
            if window:
                lo, hi = _t(window[0]), _t(window[1])
                if not (lo <= ts.time() <= hi):
                    return False, "outside_sell_window"
            return True, s
        return False, s


def _t(hhmm: str) -> time:
    h, m = (int(x) for x in str(hhmm).split(":")[:2])
    return time(h, m)


@dataclass
class Clock:
    """Injectable clock so tests are deterministic."""
    fixed: Optional[datetime] = None

    def now(self) -> datetime:
        return self.fixed or now_tehran()

