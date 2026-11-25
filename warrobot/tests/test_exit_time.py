"""Time exit in exit_engine: TSE session counting + decision (Hark, 2026-11-26)."""
from datetime import date

import exit_engine as ee

HOL_WED = {"1405-09-18"}      # 2026-12-09, a Wednesday (sample holiday)


def test_weekend_not_counted():
    # entry Mon 2026-11-23; Tue 24, Wed 25 are sessions; Thu 26 / Fri 27 are not
    assert ee.sessions_after(date(2026, 11, 23), date(2026, 11, 26), set()) == 2
    assert ee.sessions_after(date(2026, 11, 23), date(2026, 11, 27), set()) == 2
    assert ee.sessions_after(date(2026, 11, 23), date(2026, 11, 28), set()) == 3   # Sat
    assert ee.sessions_after(date(2026, 11, 23), date(2026, 11, 23), set()) == 0   # entry day


def test_holiday_skipped():
    assert ee._jalali(date(2026, 12, 9)) == "1405-09-18" and date(2026, 12, 9).weekday() == 2
    a = ee.sessions_after(date(2026, 12, 6), date(2026, 12, 13), set())
    b = ee.sessions_after(date(2026, 12, 6), date(2026, 12, 13), HOL_WED)
    assert a - b == 1


def test_fifteenth_session():
    # entry Mon 2026-11-23 -> 15th session after it, 5 sessions a week
    d, n = date(2026, 11, 23), 0
    from datetime import timedelta
    while n < 15:
        d += timedelta(days=1)
        if ee.is_session(d, set()):
            n += 1
    assert d == date(2026, 12, 14)
    assert ee.sessions_after(date(2026, 11, 23), d, set()) == 15
    assert ee.sessions_after(date(2026, 11, 23), d - timedelta(days=1), set()) == 14


def _pos(sym="آباد", qty=1000, avg=100.0):
    return {"symbol": sym, "qty": qty, "avg_cost": avg}


def _lot(at, qty=1000, buy=100.0):
    return {"qty": qty, "buy": buy, "cost": buy * 1.0089, "at": at}


def test_time_exit_due_and_not_due():
    lots = [_lot("2026-11-22 22:12:54")]            # server clock (Pacific) -> Tehran Mon 2026-11-23
    d = ee.evaluate(_pos(), lots, 100.0, 1e9, 9.0, "2026-12-14", hold=15,
                    today_tehran=date(2026, 12, 14), holidays=set())
    assert d.action == "sell" and d.reason == "time_exit" and d.due_qty == 1000
    d = ee.evaluate(_pos(), lots, 100.0, 1e9, 9.0, "2026-12-13", hold=15,
                    today_tehran=date(2026, 12, 13), holidays=set())
    assert d.action == "hold" and d.lots[0]["sessions_held"] == 14


def test_stop_loss_wins_and_manual_only_skipped():
    lots = [_lot("2026-11-01 01:00:00")]
    d = ee.evaluate(_pos(), lots, 80.0, 1e9, 9.0, "2026-12-14", hold=15,
                    today_tehran=date(2026, 12, 14), holidays=set())
    assert d.reason == "stop_loss"
    d = ee.evaluate(_pos("افران"), lots, 100.0, 1e9, 9.0, "2026-12-14", hold=15,
                    today_tehran=date(2026, 12, 14), holidays=set())
    assert d.action == "skip" and d.reason == "manual_only"


def test_unknown_entry_never_time_exits():
    # no journal lot: shares judged on the broker average only, never time-exited
    d = ee.evaluate(_pos(), [], 100.0, 1e9, 9.0, "2027-06-01", hold=15,
                    today_tehran=date(2027, 6, 1), holidays=set())
    assert d.action == "hold" and "time_exit_entry_unknown" in d.note
    d = ee.evaluate(_pos(), [_lot("")], 100.0, 1e9, 9.0, "2027-06-01", hold=15,
                    today_tehran=date(2027, 6, 1), holidays=set())
    assert d.action == "hold" and "time_exit_entry_unknown" in d.note


def test_no_calendar_means_no_time_exit():
    lots = [_lot("2026-01-01 10:00:00")]
    d = ee.evaluate(_pos(), lots, 100.0, 1e9, 9.0, "2027-06-01", hold=15,
                    today_tehran=date(2027, 6, 1), holidays=None)
    assert d.action == "hold"


def test_hold_zero_is_old_behaviour():
    lots = [_lot("2026-01-01 10:00:00")]
    d = ee.evaluate(_pos(), lots, 100.0, 1e9, 9.0, "2027-06-01")
    assert d.action == "hold"


def test_time_exit_price_is_best_bid_else_last():
    assert ee.sell_limit("time_exit", 100.0, {"floor": 97, "ceiling": 103, "bids": [[99, 10]]}) == (99, "best_bid")
    assert ee.sell_limit("time_exit", 100.0, {"floor": 97, "ceiling": 103, "bids": []}) == (100, "last")
