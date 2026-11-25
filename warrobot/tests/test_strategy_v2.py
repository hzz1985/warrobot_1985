import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import strategy_v2 as S  # noqa: E402


class StrategyV2Test(unittest.TestCase):
    def test_v1_default_is_legacy(self):
        p = S.live_params(Path(__file__).resolve().parents[1] / "config.json")
        self.assertEqual(p["version"], "v1")
        self.assertEqual((p["tp_pct"], p["sl_pct"], p["slots"]), (25.0, 3.0, 3))

    def test_v2_params(self):
        cfg = {"sell": {}, "strategy": {"version": "v2", "v2": {"max_positions": 10, "sl_limits": 3.0}}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(cfg, fh)
        p = S.live_params(fh.name)
        self.assertEqual(p["version"], "v2")
        self.assertAlmostEqual(p["sl_pct"], 9.0)
        self.assertIsNone(p["tp_pct"])
        self.assertEqual(p["slots"], 10)

    def test_floor_locked_day_is_unsellable(self):
        cfg = S.V2Config()
        bar = dict(py=100.0, o=97.0, h=97.0, l=97.0, c=97.0)
        self.assertEqual(S.exit_decision(cfg, 0.80, 1.0, 1, bar, 0.03), (None, None))

    def test_gap_stop_fills_at_open(self):
        cfg = S.V2Config()
        bar = dict(py=100.0, o=98.0, h=99.0, l=97.0, c=97.5)
        reason, x = S.exit_decision(cfg, 0.92, 1.0, 1, bar, 0.03)   # stop = 0.91
        self.assertEqual(reason, "stop_gap")
        self.assertAlmostEqual(x, 0.92 * 0.98)

    def test_time_exit(self):
        cfg = S.V2Config(max_hold=3)
        bar = dict(py=100.0, o=100.0, h=101.0, l=99.5, c=100.5)
        self.assertEqual(S.exit_decision(cfg, 1.0, 1.0, 2, bar, 0.03)[0], "time")


if __name__ == "__main__":
    unittest.main()
