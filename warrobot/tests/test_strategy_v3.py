import json, math, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import strategy_v2 as S2  # noqa: E402
import strategy_v3 as S3  # noqa: E402

NAN = float("nan")


def row(sym, value, pe=NAN, bp=NAN, **kw):
    r = dict(sym=sym, lock_up=True, prev_lock_up=False, value=value, floor_lock_recent=False,
             pe=pe, growth=NAN, bp=bp, inflow=NAN, inflow5=NAN, sec_mom=NAN)
    r.update(kw)
    return r


class V3Tests(unittest.TestCase):
    def test_defaults_are_proposed_v3(self):
        c = S3.V3Config()
        self.assertEqual((c.fund_mode, c.rank_by, c.max_positions, c.max_hold, c.sl_limits), ("exclude_loss", "bp", 10, 15, 3.0))

    def test_exclude_known_loss_keeps_unknown(self):
        c = S3.V3Config()
        self.assertFalse(S3.passes(row("a", 3e10, pe=-1.0), c))
        self.assertTrue(S3.passes(row("b", 3e10, pe=NAN), c))
        self.assertFalse(S3.passes(row("b", 3e10, pe=NAN), S3.V3Config(fund_mode="require_profit")))

    def test_rank_by_bp_vs_value(self):
        rows = [row("big", 9e10, pe=5, bp=1.1), row("mid", 5e10, pe=5, bp=4.0), row("small", 2.5e10, pe=5, bp=2.0),
                row("loss", 8e10, pe=-1, bp=9.0), row("thin", 1e10, pe=5, bp=9.0)]
        got = [r["sym"] for r in S3.pick_candidates(rows, S3.V3Config(max_picks=3), True, set())]
        self.assertEqual(got, ["mid", "small", "big"])          # loss-maker and <20bn dropped
        got = [r["sym"] for r in S3.pick_candidates(rows, S3.V3Config(rank_by="value", fund_mode="off", max_picks=2), True, set())]
        self.assertEqual(got, ["big", "loss"])                  # == v2 behaviour

    def test_regime_off_blocks(self):
        self.assertEqual(S3.pick_candidates([row("a", 3e10, pe=5, bp=2)], S3.V3Config(), False, set()), [])

    def test_live_params_v3_uses_v2_exits(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"sell": {}, "strategy": {"version": "v3", "v2": {"max_positions": 10}}}))
            lp = S2.live_params(p)
            self.assertEqual((lp["version"], lp["strategy"], lp["sl_pct"], lp["slots"]), ("v2", "v3", 9.0, 10))
            p.write_text(json.dumps({"sell": {}, "strategy": {"version": "v1"}}))
            self.assertEqual(S2.live_params(p)["version"], "v1")

    def test_filter_scan_non_v3_delegates(self):
        import pandas as pd
        cands = pd.DataFrame([{"symbol": "x", "turnover": 1.0, "close": 100.0}])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"strategy": {"version": "v1"}}))
            out = S3.filter_scan(cands, p, d)
            self.assertEqual(len(out), 1)                          # v1: untouched

    def test_from_config_merges_v2_and_v3(self):
        c = S3.V3Config.from_config({"strategy": {"v2": {"max_positions": 7}, "v3": {"rank_by": "value", "pe_max": 30}}})
        self.assertEqual((c.max_positions, c.rank_by, c.pe_max), (7, "value", 30))


if __name__ == "__main__":
    unittest.main()
