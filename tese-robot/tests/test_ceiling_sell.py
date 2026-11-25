"""A stock locked at the ceiling must be sold AT the ceiling (queue match)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tsebot.broker_base import SymbolInfo  # noqa: E402
from tsebot.engine import Engine  # noqa: E402


class _Cfg:
    mode = "paper"

    def get(self, key, default=None):
        return {"fees.sell_pct": 0.5, "sell.price_mode": "last",
                "sell.stop_price_mode": "floor", "sell.price_offset_pct": 0.0}.get(key, default)

    def section(self, name):
        return {}

    def is_live(self):
        return False

    def path(self, name):
        return Path(".")


class _Broker:
    def __init__(self):
        self.last_call = None

    def submit(self, side, symbol, qty, price):
        self.last_call = (side, symbol, qty, price)
        return {"placed": True, "price": price}


class _Decision:
    symbol = "وسرمد"
    qty = 1000
    reason = "target"
    price_hint = 1844.0
    blocked = ""
    pnl_pct = 7.0


class CeilingSellTest(unittest.TestCase):
    def _engine(self, info):
        b = _Broker()
        eng = Engine(_Cfg(), b, logger=lambda m: None)
        eng.market_quote = lambda s: info          # noqa: E731
        eng._can_attempt = lambda *a, **k: (True, "")  # noqa: E731
        eng.orders = type("O", (), {"create": lambda *a, **k: type(
            "I", (), {"ref": "r"})(),
            "set_status": lambda *a, **k: None})()
        eng.ledger = type("L", (), {"record_fill": lambda *a, **k: None})()
        eng._verify_intent = lambda *a, **k: {"ok": True}
        return eng, b

    def test_locked_at_ceiling_sells_at_ceiling(self):
        info = SymbolInfo(symbol="وسرمد", isin="X", last=1850.0, ceiling=1850.0,
                          floor=1744.0, prev_close=1800.0, max_qty=0, min_qty=1)
        eng, broker = self._engine(info)
        eng.execute_exit(_Decision(), dry=False)
        self.assertEqual(broker.last_call[3], 1850.0)

    def test_below_ceiling_keeps_hint(self):
        info = SymbolInfo(symbol="وسرمد", isin="X", last=1800.0, ceiling=1850.0,
                          floor=1744.0, prev_close=1800.0, max_qty=0, min_qty=1)
        eng, broker = self._engine(info)
        eng.execute_exit(_Decision(), dry=False)
        self.assertLess(broker.last_call[3], 1850.0)


if __name__ == "__main__":
    unittest.main()


class BestDemandTest(unittest.TestCase):
    def test_sells_at_best_bid_when_queue_is_one_tick_lower(self):
        from tsebot.broker_base import SymbolInfo as SI
        info = SI(symbol="وسرمد", isin="X", last=1845.0, ceiling=1850.0,
                  floor=1744.0, prev_close=1800.0, max_qty=0, min_qty=1, best_bid=1845.0)
        eng, broker = CeilingSellTest()._engine(info)
        eng.execute_exit(_Decision(), dry=False)
        self.assertEqual(broker.last_call[3], 1845.0)

    def test_never_exceeds_the_ceiling(self):
        from tsebot.broker_base import SymbolInfo as SI
        info = SI(symbol="وسرمد", isin="X", last=1850.0, ceiling=1850.0,
                  floor=1744.0, prev_close=1800.0, max_qty=0, min_qty=1, best_bid=1850.0)
        eng, broker = CeilingSellTest()._engine(info)
        eng.execute_exit(_Decision(), dry=False)
        self.assertEqual(broker.last_call[3], 1850.0)
