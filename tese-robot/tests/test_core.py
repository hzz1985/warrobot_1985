# -*- coding: utf-8 -*-
"""Unit tests for the invariants that v1 violated.

Run:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import shutil
import uuid
import sys
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

_TMP_BASE = Path(__file__).resolve().parent / "_tmp"
_TMP_BASE.mkdir(exist_ok=True)

from tsebot.broker_base import SymbolInfo  # noqa: E402
from tsebot.calendar_ir import TEHRAN, TradingCalendar  # noqa: E402
from tsebot.ledger import Ledger, dedupe_closed_positions  # noqa: E402
from tsebot.orders import BrokerOrder, OrderStore, match_intent  # noqa: E402
from tsebot.risk import choose_price, decide_exit, plan_buy  # noqa: E402


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.dir = _TMP_BASE / ("t" + uuid.uuid4().hex[:10])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "ledger.jsonl"
        self.led = Ledger(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_buy_sell_roundtrip(self):
        self.led.record_fill("A", "buy", 1000, 1000, fee=6000, order_no="o1")
        pos = self.led.get("A")
        self.assertEqual(pos.qty, 1000)
        self.assertAlmostEqual(pos.avg_cost, 1006.0, places=2)
        self.led.record_fill("A", "sell", 1000, 1100, fee=6600, order_no="o2")
        pos = self.led.get("A")
        self.assertEqual(pos.qty, 0)
        # proceeds 1,100,000 - 6,600 = 1,093,400 ; cost 1,000,600
        self.assertAlmostEqual(pos.realized, 87_400, places=0)

    def test_duplicate_fill_is_idempotent(self):
        self.led.record_fill("A", "buy", 500, 1000, order_no="dup")
        self.led.record_fill("A", "buy", 500, 1000, order_no="dup")
        self.assertEqual(self.led.get("A").qty, 500)

    def test_reload_keeps_state(self):
        self.led.record_fill("A", "buy", 100, 2000, order_no="x")
        again = Ledger(self.path)
        self.assertEqual(again.get("A").qty, 100)

    def test_oversell_does_not_go_negative(self):
        self.led.record_fill("A", "buy", 100, 1000, order_no="b")
        self.led.record_fill("A", "sell", 300, 1100, order_no="s")
        self.assertEqual(self.led.get("A").qty, 0)

    def test_diff_vs_broker(self):
        self.led.record_fill("A", "buy", 100, 1000, order_no="b")
        d = self.led.diff_vs_broker({"A": 100, "B": 50})
        self.assertIn("B", d)
        self.assertNotIn("A", d)

    def test_avg_cost_after_partial_sell(self):
        self.led.record_fill("A", "buy", 100, 1000, order_no="1")
        self.led.record_fill("A", "buy", 100, 2000, order_no="2")
        self.assertAlmostEqual(self.led.get("A").avg_cost, 1500, places=2)
        self.led.record_fill("A", "sell", 100, 1600, order_no="3")
        self.assertAlmostEqual(self.led.get("A").avg_cost, 1500, places=2)
        self.assertEqual(self.led.get("A").qty, 100)


class TestOrders(unittest.TestCase):
    def setUp(self):
        self.dir = _TMP_BASE / ("t" + uuid.uuid4().hex[:10])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "orders.jsonl"
        self.st = OrderStore(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_attempts_are_numbered_not_refused(self):
        """A rejected order may be retried, but every attempt is a new ref."""
        a = self.st.create("2026-10-18", "فولاد", "buy", 100, 4000)
        b = self.st.create("2026-10-18", "فولاد", "buy", 100, 4000)
        self.assertEqual(a.ref, "2026-10-18:buy:فولاد")
        self.assertEqual(b.ref, "2026-10-18:buy:فولاد#2")
        self.assertEqual(self.st.attempts_count("2026-10-18", "فولاد"), 2)

    def test_live_intent_detected(self):
        a = self.st.create("2026-10-18", "A", "sell", 10, 100)
        # a created-but-unresolved intent already blocks a second submission
        self.assertEqual(self.st.live_for("2026-10-18", "A", "sell").status, "intent")
        self.st.set_status(a.ref, "unverified")
        live = self.st.live_for("2026-10-18", "A", "sell")
        self.assertEqual(live.status, "unverified")
        self.st.set_status(a.ref, "verified", broker_order_no="1")
        # verified is still "live" so nothing can be re-sent; the engine turns it
        # into reason=already_done
        self.assertEqual(self.st.live_for("2026-10-18", "A", "sell").status, "verified")

    def test_any_today(self):
        self.st.create("2026-10-18", "فولاد", "buy", 100, 4000)
        self.assertTrue(self.st.any_today("2026-10-18", "فولاد", "buy"))
        self.assertFalse(self.st.any_today("2026-10-18", "فولاد", "sell"))

    def test_reload(self):
        self.st.create("2026-10-18", "A", "sell", 10, 100)
        st2 = OrderStore(self.path)
        self.assertIn("2026-10-18:sell:A", st2.intents)

    def test_match_skips_used_order_no(self):
        intent = self.st.create("2026-10-18", "A", "buy", 100, 1000)
        rows = [BrokerOrder("1", "1405/07/25", "A", "buy", 100, 1000, 100, "انجام")]
        self.assertIsNotNone(match_intent(intent, rows))
        self.assertIsNone(match_intent(intent, rows, used_order_nos={"1"}))

    def test_match_never_crosses_side(self):
        intent = self.st.create("2026-10-18", "A", "buy", 100, 1000)
        rows = [BrokerOrder("1", "1405/07/25", "A", "sell", 100, 1000, 100, "انجام")]
        self.assertIsNone(match_intent(intent, rows))


class TestCalendar(unittest.TestCase):
    def setUp(self):
        self.cal = TradingCalendar()

    def test_thursday_and_friday_closed(self):
        # 2026-10-15 is a Thursday, 2026-10-16 a Friday
        self.assertFalse(self.cal.is_trading_day(date(2026, 10, 15))[0])
        self.assertFalse(self.cal.is_trading_day(date(2026, 10, 16))[0])
        # 2026-10-17 is a Saturday
        self.assertTrue(self.cal.is_trading_day(date(2026, 10, 17))[0])

    def test_session_boundaries(self):
        mkt = {"open": "09:00", "close_auction_start": "12:00", "close": "12:30",
               "pre_open_start": "08:30"}
        d = date(2026, 10, 17)
        self.assertEqual(self.cal.session(mkt, datetime.combine(d, time(8, 0), TEHRAN)), "before_pre_open")
        self.assertEqual(self.cal.session(mkt, datetime.combine(d, time(8, 45), TEHRAN)), "pre_open")
        self.assertEqual(self.cal.session(mkt, datetime.combine(d, time(10, 0), TEHRAN)), "continuous")
        self.assertEqual(self.cal.session(mkt, datetime.combine(d, time(12, 10), TEHRAN)), "closing_auction")
        self.assertEqual(self.cal.session(mkt, datetime.combine(d, time(13, 0), TEHRAN)), "after_close")

    def test_no_sell_before_open(self):
        mkt = {"open": "09:00", "close_auction_start": "12:00", "close": "12:30",
               "pre_open_start": "08:30"}
        d = date(2026, 10, 17)
        ok, why = self.cal.is_open_for_sell(mkt, datetime.combine(d, time(3, 30), TEHRAN))
        self.assertFalse(ok)
        self.assertEqual(why, "before_pre_open")

    def test_next_trading_day_skips_weekend(self):
        self.assertEqual(self.cal.next_trading_day(date(2026, 10, 15)), date(2026, 10, 17))

    def test_trading_days_between(self):
        # Sat 10-17 -> Mon 10-19 includes Sunday only
        self.assertEqual(self.cal.trading_days_between(date(2026, 10, 17), date(2026, 10, 19)), 2)


class TestRisk(unittest.TestCase):
    def _info(self, **kw):
        base = dict(symbol="A", last=1000, best_bid=999, best_ask=1000,
                    ceiling=1050, floor=950, min_qty=1, max_qty=100000)
        base.update(kw)
        return SymbolInfo(**base)

    def test_never_defaults_to_ceiling(self):
        info = self._info()
        self.assertEqual(choose_price(info, "best_bid"), 999)
        self.assertLess(choose_price(info, "last"), info.ceiling)

    def test_position_cap_limits_size(self):
        plan = plan_buy(self._info(), equity=1_000_000_000, cash=1_000_000_000,
                        exposure=0, open_positions=0, slots=3, max_positions=3,
                        max_value_per_position_pct=10, max_total_exposure_pct=90,
                        min_cash_buffer_pct=5, deploy_fraction=1.0,
                        min_order_rial=5_000_000, min_order_rial_base=50_000_000,
                        price_mode="last", price_offset_pct=0)
        # 10% of 1e9 = 1e8 -> 100000 shares at 1000
        self.assertTrue(plan.ok)
        self.assertLessEqual(plan.value_rial, 100_000_000)

    def test_equal_money_slot_sizing(self):
        # 3 slots, 2 already held, 1 free: the new buy must target equity/3,
        # not the whole remaining cash.
        plan = plan_buy(self._info(last=1000, best_bid=999, best_ask=1000, ceiling=1050, floor=950), equity=900_000_000,
                        cash=900_000_000, exposure=600_000_000,
                        open_positions=2, slots=1, max_positions=3,
                        max_value_per_position_pct=90, max_total_exposure_pct=100,
                        min_cash_buffer_pct=0, deploy_fraction=1.0,
                        min_order_rial=5_000_000, min_order_rial_base=50_000_000,
                        price_mode="last", price_offset_pct=0)
        self.assertTrue(plan.ok)
        self.assertLessEqual(plan.value_rial, 300_000_000 + 1000)
        self.assertAlmostEqual(plan.target_value, 300_000_000, delta=2000)

    def test_equal_money_capped_by_cash(self):
        # same slot target, but only 150M cash available -> cannot exceed cash
        plan = plan_buy(self._info(last=1000, best_bid=999, best_ask=1000, ceiling=1050, floor=950), equity=900_000_000,
                        cash=150_000_000, exposure=600_000_000,
                        open_positions=2, slots=1, max_positions=3,
                        max_value_per_position_pct=90, max_total_exposure_pct=100,
                        min_cash_buffer_pct=0, deploy_fraction=1.0,
                        min_order_rial=5_000_000, min_order_rial_base=50_000_000,
                        price_mode="last", price_offset_pct=0)
        self.assertTrue(plan.ok)
        self.assertLessEqual(plan.value_rial, 150_000_000)

    def test_no_slot_rejected(self):
        plan = plan_buy(self._info(), equity=1e9, cash=1e9, exposure=0,
                        open_positions=3, slots=0, max_positions=3, max_value_per_position_pct=40,
                        max_total_exposure_pct=90, min_cash_buffer_pct=5,
                        deploy_fraction=1, min_order_rial=5_000_000,
                        min_order_rial_base=50_000_000, price_mode="last",
                        price_offset_pct=0)
        self.assertFalse(plan.ok)
        self.assertEqual(plan.reason, "no_free_slot")

    def test_stop_on_cumulative_basis(self):
        # current price is only -1%, but since purchase the position is -3.2%
        # (a partial sell had booked a loss). The cumulative basis must trigger.
        d = decide_exit(symbol="A", qty=100, avg_cost=1000, last_price=990,
                        days_held=2, peak_pnl_pct=6.0, info=self._info(last=990),
                        target_pct=12, stop_pct=3, max_hold_days=10,
                        trailing_stop_pct=None, trailing_arm_pct=5,
                        exit_on_queue_break=False, was_locked_up=False,
                        total_pnl_pct=-3.2)
        self.assertIsNotNone(d)
        self.assertEqual(d.reason, "stop")
        self.assertAlmostEqual(d.pnl_pct, -3.2, places=1)

    def test_target_on_cumulative_basis(self):
        # current move is only +2%, but lifetime P&L is +12.5% -> take profit
        d = decide_exit(symbol="A", qty=100, avg_cost=1000, last_price=1020,
                        days_held=1, peak_pnl_pct=13.0, info=self._info(last=1020),
                        target_pct=12, stop_pct=3, max_hold_days=10,
                        trailing_stop_pct=None, trailing_arm_pct=5,
                        exit_on_queue_break=False, was_locked_up=False,
                        total_pnl_pct=12.5)
        self.assertIsNotNone(d)
        self.assertEqual(d.reason, "target")

    def test_stop_exit(self):
        d = decide_exit(symbol="A", qty=100, avg_cost=1000, last_price=965,
                        days_held=0, peak_pnl_pct=1.0, info=self._info(last=965),
                        target_pct=10, stop_pct=3, max_hold_days=10,
                        trailing_stop_pct=None, trailing_arm_pct=5,
                        exit_on_queue_break=False, was_locked_up=False)
        self.assertIsNotNone(d)
        self.assertEqual(d.reason, "stop")

    def test_target_exit(self):
        d = decide_exit(symbol="A", qty=100, avg_cost=1000, last_price=1110,
                        days_held=1, peak_pnl_pct=11.0, info=self._info(last=1110),
                        target_pct=10, stop_pct=3, max_hold_days=10,
                        trailing_stop_pct=None, trailing_arm_pct=5,
                        exit_on_queue_break=False, was_locked_up=False)
        self.assertEqual(d.reason, "target")

    def test_sell_queue_is_blocked_not_spammed(self):
        info = self._info(last=950, floor=950, best_bid=None)
        d = decide_exit(symbol="A", qty=100, avg_cost=1000, last_price=950,
                        days_held=2, peak_pnl_pct=0.0, info=info,
                        target_pct=10, stop_pct=3, max_hold_days=10,
                        trailing_stop_pct=None, trailing_arm_pct=5,
                        exit_on_queue_break=False, was_locked_up=False)
        self.assertIsNotNone(d)
        self.assertEqual(d.blocked, "sell_queue")

    def test_hold_when_nothing_triggers(self):
        d = decide_exit(symbol="A", qty=100, avg_cost=1000, last_price=1010,
                        days_held=1, peak_pnl_pct=1.0, info=self._info(last=1010),
                        target_pct=10, stop_pct=3, max_hold_days=10,
                        trailing_stop_pct=None, trailing_arm_pct=5,
                        exit_on_queue_break=False, was_locked_up=False)
        self.assertIsNone(d)


class TestDedupe(unittest.TestCase):
    def test_closed_positions_dedupe(self):
        rows = [{"symbol": "K", "exit_at": "t", "count": 1, "exit_price": 100}] * 3
        self.assertEqual(len(dedupe_closed_positions(rows)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)








