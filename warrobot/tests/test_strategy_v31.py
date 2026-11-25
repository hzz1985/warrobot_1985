"""V3.1 Codal quality guards: parsing of real Codal reports, filters, fail-open live path."""
import json, math, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import codal_fund as C  # noqa: E402
import strategy_v3 as S3  # noqa: E402

NAN = float("nan")
FX = json.loads((HERE / "fixtures" / "codal_samples.json").read_text(encoding="utf-8"))


def row(sym="a", value=3e10, **kw):
    r = dict(sym=sym, lock_up=True, prev_lock_up=False, value=value, floor_lock_recent=False,
             pe=5.0, growth=NAN, bp=2.0, inflow=NAN, inflow5=NAN, sec_mom=NAN,
             pe_c=NAN, roe=NAN, npm=NAN, np_yoy=NAN, sales_yoy=NAN, capr_days=NAN)
    r.update(kw)
    return r


class ParseTests(unittest.TestCase):
    def test_statement_abad_6m(self):
        st = C.parse_statement(FX["stmt_abad_6m"])
        self.assertEqual((st["pend"], st["months"]), ("2026-09-22", 6))
        self.assertEqual((st["np_cur"], st["np_prev"], st["np_fy"]), (52030.0, 6190.0, 25818.0))
        m = C.statement_metrics(st)
        self.assertAlmostEqual(m["np_ttm"], 52030 + 25818 - 6190)          # TTM = cur + FY - prev
        self.assertAlmostEqual(m["roe"], 71658 / 11913157, places=6)        # ~0.6 %  (the آباد case)
        self.assertLess(m["roe"], 0.01)
        self.assertEqual(m["equity"], 11913157.0)

    def test_monthly_foolad(self):
        mo = C.parse_monthly(FX["monthly_foolad"])
        self.assertEqual((mo["sales_ytd"], mo["sales_ytd_ly"]), (3172061674.0, 1892095932.0))
        self.assertAlmostEqual(mo["sales_yoy"], 3172061674 / 1892095932 - 1)

    def test_numbers_and_dates(self):
        self.assertEqual(C.num("(۱۲,۳۴۵)"), -12345.0)
        self.assertTrue(math.isnan(C.num("")))
        self.assertEqual(C.jdate("۱۴۰۳/۰۱/۰۱"), "2024-03-20")
        self.assertEqual(C.period_of("صورت‌های مالی  سال مالی منتهی به ۱۴۰۴/۱۲/۲۹ (حسابرسی نشده)"), ("2026-03-20", 12))

    def test_first_publication_wins(self):
        L = [{"TracingNo": 2, "Title": "اطلاعات و صورت‌های مالی میاندوره‌ای دوره ۳ ماهه منتهی به ۱۴۰۴/۰۳/۳۱ (اصلاحیه)",
              "PublishDateTime": "۱۴۰۴/۰۵/۱۰ ۱۰:۰۰:۰۰", "HasExcel": True, "ExcelUrl": "u2"},
             {"TracingNo": 1, "Title": "اطلاعات و صورت‌های مالی میاندوره‌ای دوره ۳ ماهه منتهی به ۱۴۰۴/۰۳/۳۱",
              "PublishDateTime": "۱۴۰۴/۰۴/۳۰ ۱۰:۰۰:۰۰", "HasExcel": True, "ExcelUrl": "u1"}]
        got = C.first_publications(L)
        self.assertEqual([x["TracingNo"] for x in got], [1])


class FilterTests(unittest.TestCase):
    def test_defaults_off(self):
        c = S3.V3Config()
        self.assertTrue(all(getattr(c, k) is None for k in ("pe_ttm_max", "roe_min", "npm_min", "np_yoy_min",
                                                            "sales_yoy_min", "capraise_reserve_days")))
        self.assertTrue(S3.passes(row(pe_c=500.0, roe=0.001), c))           # off = live V3 unchanged

    def test_guards(self):
        c = S3.V3Config(pe_ttm_max=100.0, roe_min=0.03, np_yoy_min=-0.5)
        self.assertFalse(S3.passes(row(pe_c=440.0, roe=0.006), c))         # آباد-like
        self.assertFalse(S3.passes(row(pe_c=-1.0), c))                     # TTM loss
        self.assertFalse(S3.passes(row(pe_c=20.0, roe=0.2, np_yoy=-0.7), c))
        self.assertTrue(S3.passes(row(pe_c=20.0, roe=0.2, np_yoy=0.1), c))
        self.assertTrue(S3.passes(row(), c))                                # unknown -> pass (fail open)

    def test_sales_and_capraise(self):
        self.assertFalse(S3.passes(row(sales_yoy=-0.1), S3.V3Config(sales_yoy_min=0.0)))
        c = S3.V3Config(capraise_reserve_days=120)
        self.assertFalse(S3.passes(row(capr_days=30.0), c))
        self.assertTrue(S3.passes(row(capr_days=200.0), c))

    def test_config_keys(self):
        c = S3.V3Config.from_config({"strategy": {"v3": {"pe_ttm_max": 100, "roe_min": 0.03, "bogus": 1}}})
        self.assertEqual((c.pe_ttm_max, c.roe_min), (100, 0.03))


class LiveFailOpenTests(unittest.TestCase):
    def test_live_codal_network_down_is_nan(self):
        with tempfile.TemporaryDirectory() as d:
            f = C.Fetcher(d, min_gap_s=0, budget_s=5)
            with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
                out = S3.live_codal("آباد", 2600.0, 11862388000000.0, f, True)
        self.assertTrue(all(math.isnan(v) for v in out.values()))
        self.assertTrue(S3.passes(row(**out), S3.V3Config(pe_ttm_max=100.0, roe_min=0.03)))

    def test_live_codal_from_cache(self):
        with tempfile.TemporaryDirectory() as d:
            f = C.Fetcher(d, min_gap_s=0, budget_s=5)
            L = [{"TracingNo": 7, "Title": FX["stmt_abad_6m"]["title"], "PublishDateTime": FX["stmt_abad_6m"]["pub"],
                  "HasExcel": True, "ExcelUrl": "x"}]
            (Path(d) / "letters" / "آباد_6.json").write_text(json.dumps({"letters": L}, ensure_ascii=False), encoding="utf-8")
            (Path(d) / "ext" / "7.json").write_text(json.dumps(FX["stmt_abad_6m"], ensure_ascii=False), encoding="utf-8")
            with mock.patch("urllib.request.urlopen", side_effect=OSError("no network needed")):
                out = S3.live_codal("آباد", 2650.0, 11862388000000.0, f, False)
        self.assertAlmostEqual(out["roe"], 71658 / 11913157, places=6)
        self.assertGreater(out["pe_c"], 400)                                # 2650 * 11.86bn / 71.66bn rial
        self.assertFalse(S3.passes(row(**out), S3.V3Config(pe_ttm_max=100.0)))

    def test_blocked_marker_stops_calls(self):
        with tempfile.TemporaryDirectory() as d:
            f = C.Fetcher(d, min_gap_s=0, budget_s=5, block_cooldown_min=1)
            import time
            (Path(d) / "BLOCKED_UNTIL").write_text(str(time.time() + 60))
            with mock.patch("urllib.request.urlopen") as u:
                self.assertIsNone(f.letters("x", 6, "1404/01/01"))
                u.assert_not_called()


class FilterScanV31Tests(unittest.TestCase):
    def test_pe_guard_with_rahavard_fallback_and_fail_open(self):
        import pandas as pd
        base = pd.DataFrame([{"symbol": s, "turnover": 5e10, "close": 1000.0} for s in ("abad", "good", "nodata")])
        fund = {"abad": {"pe": 450.0, "growth": NAN, "capital_rial": 1e13, "_asset_id": "1"},
                "good": {"pe": 8.0, "growth": 0.2, "capital_rial": 1e13, "_asset_id": "2"},
                "nodata": {"pe": NAN, "growth": NAN, "capital_rial": NAN, "_asset_id": None}}
        codal = {"abad": {"pe_c": NAN, "roe": NAN, "npm": NAN, "np_yoy": NAN, "sales_yoy": NAN},   # Codal down -> rahavard P/E
                 "good": {"pe_c": 9.0, "roe": 0.3, "npm": 0.2, "np_yoy": 0.1, "sales_yoy": NAN},
                 "nodata": {"pe_c": NAN, "roe": NAN, "npm": NAN, "np_yoy": NAN, "sales_yoy": NAN}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"strategy": {"version": "v3", "v3": {"rank_by": "value", "pe_ttm_max": 50,
                                                                          "codal_cache_dir": d}}}))
            with mock.patch.object(S3.S2, "apply_v2_filters", return_value=base), \
                    mock.patch.object(S3, "live_fundamentals", side_effect=lambda s, px: dict(fund[s])), \
                    mock.patch.object(S3, "live_codal", side_effect=lambda s, px, cap, f, w: dict(codal[s])):
                out = S3.filter_scan(base, p, d, keep_all=True)
        self.assertEqual(sorted(out.symbol), ["good", "nodata"])       # abad dropped, unknown kept (fail open)


if __name__ == "__main__":
    unittest.main()
