"""Failure path: a ticket that never reached the panel must not block retries.

This is the exact bug that stopped the وایرا sale on 2026-10-21: the submit
returned {"path": "ui", "reason": "symbol_row_not_found"}, the engine recorded it
as "submitted" (live), and the attempt policy then refused every retry for the
whole cooldown window.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tsebot.broker_base import SymbolInfo  # noqa: E402
from tsebot.engine import Engine  # noqa: E402


class _Cfg:
    mode = "live"

    def get(self, key, default=None):
        return {"fees.sell_pct": 0.5, "sell.price_mode": "best_bid",
                "sell.stop_price_mode": "floor", "sell.retry_cooldown_sec": 120,
                "sell.max_attempts_per_day": 20,
                "risk.max_orders_per_symbol_per_day": 12,
                "risk.max_daily_orders_total": 40}.get(key, default)

    def section(self, name):
        return {}

    def is_live(self):
        return True

    def path(self, name):
        return Path(".")


class _Orders:
    def __init__(self):
        self.status = {}

    def create(self, day, symbol, side, qty, price, **kw):
        return type("I", (), {"ref": f"{symbol}#1"})()

    def set_status(self, ref, status, **kw):
        self.status[ref] = status

    def attempts(self, day, symbol, side):
        return []

    def attempts_count(self, day, symbol):
        return 0

    def for_day(self, day):
        return []


class _Broker:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def submit_via_ui(self, side, symbol, qty, price):
        self.calls += 1
        return dict(self.reply)

    def row_cells(self, symbol):
        return {}


class _Decision:
    symbol = "وایرا"
    qty = 1000
    reason = "target"
    price_hint = 4100.0
    blocked = ""
    pnl_pct = 7.0


class FailedSubmitTest(unittest.TestCase):
    def _engine(self, reply):
        b = _Broker(reply)
        eng = Engine(_Cfg(), b, logger=lambda m: None)
        eng.orders = _Orders()
        eng.ledger = type("L", (), {"record_fill": lambda *a, **k: None})()
        eng._verify_intent = lambda *a, **k: {"ok": True}
        info = SymbolInfo(symbol="وایرا", isin="X", last=4100.0, ceiling=4160.0,
                          floor=3900.0, prev_close=4000.0, max_qty=0, min_qty=1)
        eng.market_quote = lambda s: info
        eng.quote = lambda s: info
        return eng, b

    def test_row_not_found_is_rejected_not_live(self):
        eng, broker = self._engine({"path": "ui", "reason": "symbol_row_not_found"})
        res = eng.execute_exit(_Decision(), dry=False)
        self.assertFalse(res.get("ok"))
        self.assertEqual(eng.orders.status.get("وایرا#1"), "rejected")
        self.assertEqual(broker.calls, 1)

    def test_sent_ticket_is_not_marked_rejected(self):
        eng, broker = self._engine({"path": "ui", "sent": True, "placed": True,
                                    "inputs": ["1,000", "4,100"]})
        eng.execute_exit(_Decision(), dry=False)
        self.assertEqual(eng.orders.status.get("وایرا#1"), "unverified")


if __name__ == "__main__":
    unittest.main()
