"""Fundamentals strategy: point-in-time EPS math, gate (the آباد case), ranking, selection buffer,
MANUAL_ONLY, capital split / V3 exclusions, live plan fail-safe, order planning."""
import json, math, sys, tempfile, unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import fund_data as FD      # noqa: E402
import strategy_fund as SF  # noqa: E402
import fund_ledger as FL    # noqa: E402
import fund_live as LV      # noqa: E402
import fund_orders as FO    # noqa: E402

NAN = float("nan")


def ev(pub, fy, m, eps, cap=1e12):
    return dict(pub=pub, fy=fy, m=m, eps=eps, capital=cap, np=eps * cap / 1000.0)


EVENTS = [ev("2024-07-20", "2025-03-20", 3, 100), ev("2024-10-20", "2025-03-20", 6, 220),
          ev("2025-01-20", "2025-03-20", 9, 330), ev("2025-06-20", "2025-03-20", 12, 450),
          ev("2025-07-20", "2026-03-20", 3, 150), ev("2025-10-20", "2026-03-20", 6, 330)]


class EpsMath(unittest.TestCase):
    def test_point_in_time(self):
        s = FD.eps_snapshot(EVENTS, "2025-10-19")         # the 6M report is not public yet
        self.assertEqual(s["months"], 3)
        self.assertAlmostEqual(s["np_ttm"], (150 + 450 - 100) * 1e9)   # TTM = cur + FY - prev
        self.assertTrue(s["ttm_exact"])
        self.assertAlmostEqual(s["yoy"], 0.5)

    def test_ttm_after_6m(self):
        s = FD.eps_snapshot(EVENTS, "2025-10-20")
        self.assertAlmostEqual(s["np_ttm"], (330 + 450 - 220) * 1e9)
        self.assertAlmostEqual(s["yoy"], 330 / 220 - 1)

    def test_annualised_when_no_prior_year(self):
        s = FD.eps_snapshot(EVENTS, "2024-11-01")
        self.assertFalse(s["ttm_exact"])
        self.assertAlmostEqual(s["np_ttm"], 220e9 * 2)
        self.assertTrue(math.isnan(s["yoy"]))

    def test_restatement_newest_version_wins(self):
        evs = EVENTS + [ev("2025-11-01", "2026-03-20", 6, 300)]
        self.assertAlmostEqual(FD.eps_snapshot(evs, "2025-10-25")["np_ttm"], (330 + 450 - 220) * 1e9)
        self.assertAlmostEqual(FD.eps_snapshot(evs, "2025-11-01")["np_ttm"], (300 + 450 - 220) * 1e9)

    def test_rahavard_document_parsing(self):
        doc = {"eps": {"eps_fiscal_years": [{"fiscal_year": "Fri 2026-03-20 12:00 AM (UTC+03:30)", "epses": [
            {"date_time": "Wed 2026-07-22 12:00 AM (UTC+03:30)", "fiscal_year": "Fri 2026-03-20 12:00 AM (UTC+03:30)",
             "fiscal_period": 0, "pure_realized_eps": 2, "capital": 11862388000000}]}]}}
        e = FD.eps_events(doc)
        self.assertEqual((e[0]["pub"], e[0]["m"]), ("2026-07-22", 12))   # AGM figure = annual
        self.assertAlmostEqual(e[0]["np"], 2 * 11862388000)

    def test_roe_asof(self):
        r = FD.ratio_events([{"date": "Sat 2024-07-20 12:00 AM", "roe": 43.66}, {"date": "Mon 2025-08-04", "roe": 37.18}])
        self.assertTrue(math.isnan(FD.roe_asof(r, "2024-07-19")))
        self.assertAlmostEqual(FD.roe_asof(r, "2025-08-04"), 0.3718)


def row(sym, pe=5.0, yoy=0.2, roe=0.3, mom=0.1, liquid=True, np_ttm=1e12, flow=NAN):
    return dict(sym=sym, liquid=liquid, np_ttm=np_ttm, mcap=np_ttm * pe if np_ttm > 0 else 1e12,
                yoy=yoy, roe=roe, mom=mom, flow=flow)


class Ranking(unittest.TestCase):
    cfg = SF.VARIANTS["F3_QVGM"]

    def test_abad_case_rejected(self):
        self.assertFalse(SF.gate(row("آباد", pe=440, roe=0.006), self.cfg))       # P/E 440 > 40
        self.assertFalse(SF.gate(row("زیان", np_ttm=-5e9), self.cfg))              # TTM loss
        self.assertFalse(SF.gate(row("کم", liquid=False), self.cfg))
        self.assertTrue(SF.gate(row("خوب", pe=8), self.cfg))

    def test_manual_only_never(self):
        for s in SF.MANUAL_ONLY:
            self.assertFalse(SF.gate(row(s, pe=2, yoy=5, roe=0.9, mom=3), self.cfg))
        ranked = SF.rank([row("الف"), row("افران", pe=1)], self.cfg)
        self.assertEqual([r["sym"] for r in ranked], ["الف"])

    def test_rank_order_and_missing_neutral(self):
        rows = [row("a", pe=4, yoy=0.5, roe=0.4, mom=0.3), row("b", pe=20, yoy=-0.2, roe=0.05, mom=-0.1),
                row("c", pe=8, yoy=NAN, roe=NAN, mom=NAN)]
        r = SF.rank(rows, self.cfg)
        self.assertEqual([x["sym"] for x in r], ["a", "c", "b"])
        self.assertEqual(SF.pct_rank([1.0, NAN, 3.0]), [0.0, 0.5, 1.0])

    def test_select_buffer(self):
        ranked = [dict(sym=f"s{i}", rank=i) for i in range(1, 31)]
        cfg = SF.VARIANTS["F3_QVGM"]
        t = SF.select(ranked, ["s15", "s25"], cfg)       # s15 inside buffer 20 -> kept; s25 dropped
        self.assertIn("s15", t)
        self.assertNotIn("s25", t)
        self.assertEqual(len(t), 10)

    def test_config_defaults_off(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"strategy": {"version": "v3"}}), encoding="utf-8")
            fc = FL.fund_config(p)
            self.assertFalse(fc["enabled"])
            self.assertFalse(fc["live_orders"])
            raw, cfg = SF.load_config(p)
            self.assertEqual(cfg.variant, "F3_QVGM")
        repo_cfg = FL.fund_config(HERE.parent / "config.json")   # shipped LIVE since 2026-11-28 (owner order)
        self.assertTrue(repo_cfg["enabled"]); self.assertEqual(repo_cfg["mode"], "replace")
        self.assertTrue(repo_cfg["manage_afran"]); self.assertAlmostEqual(repo_cfg["afran_share"], 1 / 3, places=6)


def _sandbox(fund_cfg, positions=None, cash=None, state=None, v3=None, plan=None):
    d = Path(tempfile.mkdtemp())
    strat = {"fund": fund_cfg}
    if v3 is not None:
        strat["v3"] = v3
    (d / "config.json").write_text(json.dumps({"strategy": strat}, ensure_ascii=False), encoding="utf-8")
    st = d / "state"; (st / "webdata").mkdir(parents=True)
    if positions is not None:
        (st / "webdata" / "portfolio.json").write_text(json.dumps(
            {"at": "2026-11-27 23:00:00", "positions": [{"symbol": s, "qty": q, "price": px, "value": q * px}
                                                       for s, (q, px) in positions.items()]}, ensure_ascii=False),
            encoding="utf-8")
    if cash is not None:
        (st / "webdata" / "balance.json").write_text(json.dumps({"at": "2026-11-27 23:00:00",
                                                                 "account_balance": cash}), encoding="utf-8")
    if state is not None:
        (st / "fund_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    if plan is not None:
        (st / "fund_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return d, st, [mock.patch.object(FL, "BASE", d), mock.patch.object(FL, "STATE", st),
                   mock.patch.object(FL, "LEDGER", st / "fund_positions.json"),
                   mock.patch.object(FL, "FSTATE", st / "fund_state.json"),
                   mock.patch.object(FO, "BASE", d), mock.patch.object(FO, "STATE", st),
                   mock.patch.object(FO, "PLAN", st / "fund_plan.json"), mock.patch.object(FO, "OLOG", st / "fund_orders.jsonl"),
                   mock.patch.object(FO, "ORDERS_LOG", st / "orders_log.jsonl")]


def run_in(fn, *a, **kw):
    d, st, ps = _sandbox(*a, **kw)
    for p in ps: p.start()
    try:
        return fn()
    finally:
        for p in ps: p.stop()


class Split(unittest.TestCase):
    def run_with(self, fund_cfg, fn, ledger=None):
        d, st, ps = _sandbox(fund_cfg)
        if ledger is not None:
            (st / "fund_positions.json").write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
        for p in ps: p.start()
        try:
            return fn()
        finally:
            for p in ps: p.stop()

    def test_off_is_neutral(self):
        self.assertEqual(self.run_with({}, FL.v3_scale), 1.0)
        self.assertTrue(self.run_with({}, FL.v3_buys_enabled))
        self.assertEqual(self.run_with({}, FL.v3_excluded), set(FL.MANUAL_ONLY))

    def test_alongside_and_replace(self):
        self.assertAlmostEqual(self.run_with({"enabled": True, "capital_share": 0.25}, FL.v3_scale), 0.75)
        self.assertEqual(self.run_with({"enabled": True, "mode": "replace"}, FL.v3_scale), 0.0)
        self.assertFalse(self.run_with({"enabled": True, "mode": "replace"}, FL.v3_buys_enabled))
        self.assertAlmostEqual(self.run_with({"enabled": True, "capital_share": 0.4}, lambda: FL.fund_equity(1e10)), 4e9)
        self.assertAlmostEqual(self.run_with({"enabled": True, "mode": "replace", "manage_afran": True},
                                             lambda: FL.fund_equity(9e9)), 6e9, delta=1)

    def test_v3_archived_flag_stops_v3_buys(self):
        self.assertFalse(run_in(FL.v3_buys_enabled, {}, v3={"enabled": False}))

    def test_fund_positions_excluded_even_if_disabled(self):
        led = {"فولاد": {"qty": 100, "cost_rial": 5e5}, "افران": {"qty": 5}}
        x = self.run_with({"enabled": False}, FL.v3_excluded, ledger=led)
        self.assertIn("فولاد", x)
        self.assertEqual(self.run_with({}, FL.fund_symbols, ledger=led), ["فولاد"])   # MANUAL_ONLY never fund

    def test_replace_ownership_and_capital(self):
        cfg = {"enabled": True, "mode": "replace", "manage_afran": True}
        pos = {"افران": (39272, 57574.0), "آباد": (629746, 2595.0), "خساپا": (138299, 674.0), "فولاد": (1000, 5000.0)}
        def f():
            return FL.fund_symbols(), FL.legacy_v3(), FL.capital(1e6)
        fs, leg, cap = run_in(f, cfg, positions=pos, state={"legacy_v3": ["آباد"]})
        self.assertEqual(fs, ["فولاد"])                         # not افران / خساپا / legacy آباد
        self.assertEqual(leg, {"آباد"})
        self.assertAlmostEqual(cap["total"], 1e6 + 39272 * 57574.0 + 5e6)   # no آباد, no خساپا
        self.assertAlmostEqual(cap["afran_target"], cap["total"] / 3, delta=1)
        self.assertAlmostEqual(cap["stock_budget"], cap["total"] * 2 / 3, delta=1)

    def test_legacy_leaves_when_sold(self):
        cfg = {"enabled": True, "mode": "replace"}
        self.assertEqual(run_in(FL.legacy_v3, cfg, positions={"افران": (10, 1.0)}, state={"legacy_v3": ["آباد"]}), set())
        self.assertEqual(run_in(FL.legacy_v3, cfg, state={"legacy_v3": ["آباد"]}), {"آباد"})   # no snapshot: keep

    def test_record_fill(self):
        def f():
            FL.record_fill("شپنا", "buy", 100, 1000)
            FL.record_fill("شپنا", "sell", 40, 1100)
            return FL.load()["شپنا"]["qty"]
        self.assertEqual(self.run_with({}, f, ledger={}), 60)


class LivePlan(unittest.TestCase):
    def _hist(self, n=200, trend=0.002, val=5e10):
        rows, c = [], 1000.0
        for k in range(n):
            py = c; c = c * (1 + trend)
            rows.append([20250000 + k, py, c, 100, val])
        return rows

    def _fake(self, eps_ok=True, prices_ok=True):
        uni = {f"ins{i}": f"نماد{i}" for i in range(30)}
        def hist(ins, day):
            if not prices_ok and ins != "ins0":
                raise RuntimeError("down")
            return self._hist(trend=0.001 * (int(ins[3:]) % 7))
        class RC:
            stale = set()
            def eps(self, sym):
                if not eps_ok:
                    return []
                k = int(sym.replace("نماد", ""))
                return [ev("2025-01-01", "2025-03-20", 12, 50 + 10 * k, cap=1e12)]
            def roe(self, sym):
                return [("2025-01-01", 0.2)]
        return {"universe": lambda: uni, "history": hist, "rahavard": RC(), "codal_index": {},
                "total_assets": lambda: 1e11}

    def _run(self, **kw):
        d = tempfile.mkdtemp(); st = Path(d) / "state"; st.mkdir()
        (Path(d) / "config.json").write_text(json.dumps({"strategy": {"fund": {"enabled": True, "capital_share": 0.5}}}), encoding="utf-8")
        with mock.patch.object(FL, "BASE", Path(d)), mock.patch.object(FL, "STATE", st), \
                mock.patch.object(FL, "LEDGER", st / "fund_positions.json"), mock.patch.object(FL, "FSTATE", st / "fund_state.json"), \
                mock.patch.object(LV, "BASE", Path(d)), mock.patch.object(FL, "v3_symbols", lambda: {"نماد29"}):
            return LV.build_plan(force=True, now=datetime(2026, 11, 26, 21, 30), fetch=self._fake(**kw))

    def test_plan_rebalances(self):
        p = self._run()
        self.assertEqual(p["action"], "rebalance", p["notes"])
        self.assertEqual(len(p["target"]), 10)
        self.assertTrue(all(b["budget_rial"] == 5e9 for b in p["buys"]))       # 0.5 x 1e11 / 10
        self.assertNotIn("نماد29", [b["symbol"] for b in p["buys"]])             # V3 holding never bought

    def test_fail_safe_on_missing_fundamentals(self):
        p = self._run(eps_ok=False)
        self.assertEqual(p["action"], "hold")
        self.assertEqual(p["buys"], []); self.assertEqual(p["sells"], [])

    def test_fail_safe_on_missing_prices(self):
        p = self._run(prices_ok=False)
        self.assertEqual(p["action"], "hold")


LIVE = {"enabled": True, "mode": "replace", "manage_afran": True, "afran_share": 1 / 3, "live_orders": True,
        "min_trade_rial": 50e6, "topup_drift": 0.25, "afran_min_excess_pct": 0.02}
TEN = ["الف", "ب", "پ", "ت", "ث", "ج", "چ", "ح", "خ", "د"]
PLAN = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "action": "rebalance", "session": 20261125,
        "target": TEN + [], "ranked_top": [{"sym": s, "close": 1000.0} for s in TEN]}
PX = dict(buy_px=lambda s, r: (int(r * 1.03), "test"), sell_px=lambda s, r, floor_share=0.0: (int(r), "test"),
          ref_close=lambda s: 57574.0 if s == "افران" else 1000.0)


def plan(cash, positions, state=None, plan=PLAN, cfg=LIVE, **kw):
    return run_in(lambda: FO.plan_now(cash, [], sold_afran_today=kw.pop("sold", False), bought_today=kw.pop("bought", set()),
                                      **PX, **kw), cfg, positions=positions, state=state or {"legacy_v3": ["آباد"]},
                  plan=plan)


class Orders(unittest.TestCase):
    SAT = {"افران": (39272, 57574.0), "آباد": (629746, 2595.0), "خساپا": (138299, 674.0)}

    def test_saturday_afran_excess_and_equal_split(self):
        p = plan(2613, self.SAT, simulate_afran_sale=True)
        total = 2613 + 39272 * 57574.0
        self.assertAlmostEqual(p["capital"]["total"], total)
        a = p["afran_sell"]
        self.assertEqual(a["symbol"], "افران")
        left = (39272 - a["qty"]) * 57574.0
        self.assertGreaterEqual(left, total / 3)                 # never below 1/3
        self.assertLess(left - total / 3, 57574.0)               # sells the whole excess (to one unit)
        self.assertAlmostEqual(p["per_name"], total * 2 / 3 / 10, delta=1)
        self.assertEqual(len(p["buys"]), 10)                     # 9 full names + the 10th with what is left
        for b in p["buys"]:
            self.assertLessEqual(b["qty"] * b["price"] * (1 + FO.FEE_BUY), p["per_name"] + 1)
        self.assertLess(p["buys"][-1]["budget"], p["per_name"])  # spend cap: last one a bit short
        self.assertIsNone(p.get("waiting_for_cash"))

    def test_no_cash_no_buys_but_afran_sale(self):
        p = plan(2613, self.SAT)
        self.assertEqual(p["buys"], [])
        self.assertIsNotNone(p["afran_sell"])
        self.assertEqual(len(p["waiting_for_cash"]), 10)

    def test_afran_below_target_never_sold_and_once_a_day(self):
        pos = {"افران": (10000, 57574.0)}
        p = plan(2e9, pos)                                       # 0.576e9 of 2.576e9 < 1/3
        self.assertIsNone(p["afran_sell"])
        p = plan(0, self.SAT, sold=True)
        self.assertIsNone(p["afran_sell"])

    def test_legacy_khsapa_pending_and_drift(self):
        tgt = dict(PLAN, target=["آباد", "خساپا", "الف", "ب", "پ"])
        pos = dict(self.SAT, **{"الف": (100000, 1000.0), "ب": (140000, 1000.0)})
        p = plan(5e9, pos, plan=tgt, bought={"پ"})
        syms = [b["symbol"] for b in p["buys"]]
        self.assertNotIn("آباد", syms); self.assertNotIn("خساپا", syms); self.assertNotIn("پ", syms)
        per = p["per_name"]
        self.assertTrue(per - 100e6 >= 0.25 * per)              # الف far below target -> top-up
        self.assertIn("الف", syms)
        if per - 140e6 < 0.25 * per:
            self.assertNotIn("ب", syms)                          # small drift -> no churn

    def test_rebalance_sells_only_on_new_rebalance(self):
        pos = dict(self.SAT, **{"قدیم": (1000, 1000.0)})
        p = plan(0, pos)
        self.assertEqual([s["symbol"] for s in p["sells"]], ["قدیم"])
        p = plan(0, pos, state={"legacy_v3": ["آباد"], "last_rebalance_session": 20261125, "target": TEN})
        self.assertEqual(p["sells"], [])
        self.assertFalse(p["new_rebalance"])

    def test_arabic_persian_spelling_is_one_symbol(self):
        tgt = dict(PLAN, target=["شغدير"], ranked_top=[{"sym": "شغدير", "close": 1000.0}])
        pos = dict(self.SAT, **{"شغدیر": (520000, 1000.0)})            # broker spelling, ~at its target
        p = plan(5e9, pos, plan=tgt)
        self.assertNotIn("شغدیر", [b["symbol"] for b in p["buys"]])   # held at target -> no double buy
        p = plan(5e9, self.SAT, plan=tgt)
        self.assertEqual([b["symbol"] for b in p["buys"]], ["شغدیر"])   # orders use the broker spelling

    def test_candidates_plan_respects_live_flag(self):
        cfg = dict(LIVE, live_orders=False)
        out = run_in(lambda: FO.candidates_plan({"orders": [], "planned": []}, lambda: 5e9, lambda: [], lambda s: "IR"),
                     cfg, positions=self.SAT, state={"legacy_v3": ["آباد"]}, plan=PLAN)
        self.assertEqual(out["planned"], [])

    def test_disabled_does_nothing(self):
        with mock.patch.object(FL, "fund_config", lambda path=None: dict(FL.DEFAULTS)), \
                mock.patch.object(FO, "_flow", side_effect=AssertionError("no order when disabled")), \
                mock.patch.object(FO, "_log", lambda rec: None):
            self.assertEqual(FO.run(), {"skipped": True})


if __name__ == "__main__":
    unittest.main()
