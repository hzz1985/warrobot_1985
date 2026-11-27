"""War2 = F3_QVGM + fixed 15 % stop: trigger on the close, fee-inclusive cost, capital-raise adjustment,
floor-lock retry, no re-buy in the cycle (cash waits), exclusions, plan JSON fields, sells first, V3 exits off."""
import json, sys, tempfile, types, unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import fund_ledger as FL    # noqa: E402
import fund_live as LV      # noqa: E402
import fund_orders as FO    # noqa: E402
import war2_stop as W2      # noqa: E402

NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
TEN = ["الف", "ب", "پ", "ت", "ث", "ج", "چ", "ح", "خ", "د"]
LIVE = {"enabled": True, "mode": "replace", "manage_afran": True, "afran_share": 1 / 3, "live_orders": True,
        "min_trade_rial": 50e6, "topup_drift": 0.25, "afran_min_excess_pct": 0.02, "name": "War2",
        "stop_fixed_pct": 15}
PX = dict(buy_px=lambda s, r: (int(r * 1.03), "test"), sell_px=lambda s, r, floor_share=0.0: (int(r), "test"),
          ref_close=lambda s: 57574.0 if s == "افران" else 1000.0, stop_px=lambda s, r: (int(r * 0.97), "floor"))


def sandbox(fund_cfg, positions=None, cash=None, state=None, plan=None):
    """positions: {sym: (qty, price, avg_cost)}"""
    d = Path(tempfile.mkdtemp())
    (d / "config.json").write_text(json.dumps({"strategy": {"version": "fund", "fund": fund_cfg}}, ensure_ascii=False),
                                   encoding="utf-8")
    st = d / "state"; (st / "webdata").mkdir(parents=True)
    if positions is not None:
        (st / "webdata" / "portfolio.json").write_text(json.dumps(
            {"at": "2026-11-28 23:00:00", "positions": [
                {"symbol": s, "qty": v[0], "price": v[1], "value": v[0] * v[1], "avg_cost": v[2],
                 "avg_cost_fee": round(v[2] * 1.009)} for s, v in positions.items()]}, ensure_ascii=False),
            encoding="utf-8")
    if cash is not None:
        (st / "webdata" / "balance.json").write_text(json.dumps({"at": "2026-11-28 23:00:00",
                                                                 "account_balance": cash}), encoding="utf-8")
    (st / "fund_state.json").write_text(json.dumps(state or {"legacy_v3": ["آباد"]}, ensure_ascii=False),
                                        encoding="utf-8")
    if plan is not None:
        (st / "fund_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    patches = [mock.patch.object(FL, "BASE", d), mock.patch.object(FL, "STATE", st),
               mock.patch.object(FL, "LEDGER", st / "fund_positions.json"),
               mock.patch.object(FL, "FSTATE", st / "fund_state.json"),
               mock.patch.object(FO, "BASE", d), mock.patch.object(FO, "STATE", st),
               mock.patch.object(FO, "PLAN", st / "fund_plan.json"), mock.patch.object(FO, "OLOG", st / "fund_orders.jsonl"),
               mock.patch.object(FO, "ORDERS_LOG", st / "orders_log.jsonl"), mock.patch.object(FO, "RUN", st / "run.json"),
               mock.patch.object(LV, "BASE", d)]
    return d, st, patches


def run_in(fn, *a, **kw):
    d, st, ps = sandbox(*a, **kw)
    for p in ps: p.start()
    try:
        return fn(st)
    finally:
        for p in ps: p.stop()


def hist(n=200, start=1000.0, trend=0.0, gap_at=None, gap=1.0):
    """[[d, py, c, ntr, val]] oldest first; gap_at: session index where priceYesterday is adjusted by `gap`."""
    rows, c = [], start
    for k in range(n):
        py = c * (gap if k == gap_at else 1.0)
        c = py * (1 + trend)
        rows.append([20250000 + k, py, c, 100, 5e10])
    return rows


class Math(unittest.TestCase):
    def test_cost_includes_buy_fee(self):
        self.assertAlmostEqual(W2.entry_cost({"avg_cost": 10000})[0], 10037.12)
        self.assertEqual(W2.entry_cost({"avg_cost_fee": 10090})[0], 10090)          # fallback: broker fee avg
        self.assertEqual(W2.entry_cost({})[1], "none")

    def test_trigger_on_close(self):
        rows = hist(n=10, start=1000.0)
        sess = rows[-1][0]
        cost = 1000 / 0.85 / 1.003712            # avg such that the stop sits exactly at 1000
        r = W2.judge("x", {"qty": 10, "avg_cost": cost}, rows, sess, None, 15)
        self.assertEqual(r["status"], "triggered")           # close == stop -> triggered (<=)
        r = W2.judge("x", {"qty": 10, "avg_cost": cost * 0.99}, rows, sess, None, 15)
        self.assertEqual(r["status"], "ok")
        self.assertGreater(r["dist_pct"], 0)
        self.assertAlmostEqual(r["stop_price"], cost * 0.99 * 1.003712 * 0.85, places=1)

    def test_no_close_today_not_judged(self):
        rows = hist(n=10)
        r = W2.judge("x", {"qty": 10, "avg_cost": 5000}, rows, rows[-1][0] + 1, None, 15)
        self.assertEqual(r["status"], "no_close_today")
        r = W2.judge("x", {"qty": 10}, rows, rows[-1][0], None, 15)
        self.assertEqual(r["status"], "no_cost")

    def test_capital_raise_does_not_fire(self):
        # bonus issue: price halves on the ex-date, the broker has not credited the shares yet
        rows = hist(n=20, start=1000.0, gap_at=15, gap=0.5)
        sess = rows[-1][0]
        basis = {"cost_ps": 1003.712, "since": rows[5][0]}
        r = W2.judge("x", {"qty": 10, "avg_cost": 1000}, rows, sess, basis, 15)
        self.assertAlmostEqual(r["adj"], 0.5)
        self.assertEqual(r["status"], "ok")
        r = W2.judge("x", {"qty": 10, "avg_cost": 1000}, rows, sess, {"cost_ps": 1003.712, "since": rows[16][0]}, 15)
        self.assertAlmostEqual(r["adj"], 1.0)                 # gap before `since` is already in the basis

    def test_basis_rerecorded_on_change(self):
        b = W2.update_basis({}, {"x": {"qty": 10, "avg_cost": 100}}, 20261128)
        self.assertEqual(b["x"]["since"], 20261128)
        b2 = W2.update_basis(b, {"x": {"qty": 10, "avg_cost": 100}}, 20261129)
        self.assertEqual(b2["x"]["since"], 20261128)          # unchanged -> keeps its date
        b3 = W2.update_basis(b2, {"x": {"qty": 20, "avg_cost": 50}}, 20261130)   # bonus shares credited
        self.assertEqual(b3["x"]["since"], 20261130)

    def test_config_off_by_default_and_repo_is_war2(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"; p.write_text(json.dumps({"strategy": {"fund": {"enabled": True}}}), encoding="utf-8")
            self.assertFalse(W2.config(p)["active"])
            p.write_text(json.dumps({"strategy": {"fund": {"stop_fixed_pct": "bad"}}}), encoding="utf-8")
            self.assertEqual(W2.config(p)["stop_fixed_pct"], 0.0)
        c = W2.config(HERE.parent / "config.json")
        self.assertEqual((c["name"], c["stop_fixed_pct"]), ("War2", 15.0))
        raw = json.loads((HERE.parent / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["strategy"]["version"], "fund")   # scheduler key untouched

    def test_snapshot_avg_cost_sources(self):
        def f(st):
            (st / "pnl.json").write_text(json.dumps({"at": "2026-11-28 23:30:00", "positions": [
                {"symbol": "x", "qty": 10, "price": 90, "value": 900, "avg_cost": 101}]}), encoding="utf-8")
            return FL.positions_snapshot()[1]["x"]
        p = run_in(f, LIVE, positions={"x": (10, 90.0, 100.0)})
        self.assertNotIn("avg_cost", p)                       # pnl.json avg is the fee break-even, not raw
        self.assertEqual(p["avg_cost_fee"], 101)
        p = run_in(lambda st: FL.positions_snapshot()[1]["x"], LIVE, positions={"x": (10, 90.0, 100.0)})
        self.assertEqual(p["avg_cost"], 100.0)


class Plan(unittest.TestCase):
    """nightly plan (fund_live.build_plan) with 30 names; نماد3 is held far under water."""

    def _fetch(self, close_by_ins=None):
        uni = {f"ins{i}": f"نماد{i}" for i in range(30)}

        def h(ins, day):
            return hist(trend=0.001 * (int(ins[3:]) % 7))

        class RC:
            stale = set()
            def eps(self, sym):
                k = int(sym.replace("نماد", ""))
                return [dict(pub="2025-01-01", fy="2025-03-20", m=12, eps=50 + 10 * k, capital=1e12,
                             np=(50 + 10 * k) * 1e9)]
            def roe(self, sym):
                return [("2025-01-01", 0.2)]
        return {"universe": lambda: uni, "history": h, "rahavard": RC(), "codal_index": {},
                "total_assets": lambda: 1e11}

    def _plan(self, cfg=LIVE, state=None, force=True):
        close3 = hist(trend=0.003)[-1][2]
        pos = {"نماد3": (1000, close3, close3 / 0.80),            # -20 % -> triggered
               "نماد4": (1000, 1000.0, hist(trend=0.004)[-1][2]),  # at cost -> ok
               "افران": (1000, 57574.0, 90000.0),                 # deep loss, never judged
               "خساپا": (1000, 674.0, 5000.0),
               "آباد": (1000, 2595.0, 9000.0)}                      # legacy V3: V3 exits only
        return run_in(lambda st: LV.build_plan(force=force, now=datetime(2026, 11, 28, 21, 30), fetch=self._fetch()),
                      cfg, positions=pos, state=state or {"legacy_v3": ["آباد"]})

    def test_fields_and_exclusions(self):
        p = self._plan()
        self.assertEqual(p["war2"], {"name": "War2", "stop_fixed_pct": 15.0, "active": True})
        self.assertEqual(sorted(r["symbol"] for r in p["risk"]), ["نماد3", "نماد4"])
        st = {r["symbol"]: r for r in p["risk"]}
        self.assertEqual(st["نماد3"]["status"], "triggered")
        self.assertEqual(st["نماد4"]["status"], "ok")
        for k in ("cost_ps", "stop_price", "close", "dist_pct", "pnl_pct", "adj"):
            self.assertIn(k, st["نماد4"])
        self.assertEqual([(x["symbol"], x["status"]) for x in p["stops"]], [("نماد3", "new")])
        self.assertEqual(p["stopped_this_cycle"], ["نماد3"])
        self.assertIn("war2_basis", p)                       # persisted by main(), popped before writing

    def test_rebalance_leaves_stopped_name_out(self):
        p = self._plan()
        self.assertEqual(p["action"], "rebalance", p["notes"])
        self.assertNotIn("نماد3", p["target"])
        self.assertNotIn("نماد3", [s["symbol"] for s in p["sells"]])      # sold by the stop order only
        self.assertEqual(p["stops"][0]["cycle"], p["session"])           # stop on a rebalance night: new cycle
        for b in p["buys"]:
            self.assertAlmostEqual(b["stop_price_est"], round(b["ref_price"] * 1.003712 * 0.85), delta=1)

    def test_not_rebalance_night_still_judges(self):
        p = self._plan(state={"legacy_v3": ["آباد"], "last_rebalance_session": 20250195}, force=False)
        self.assertEqual(p["action"], "hold")
        self.assertEqual([x["symbol"] for x in p["stops"]], ["نماد3"])
        self.assertEqual(p["stops"][0]["cycle"], 20250195)

    def test_pending_stop_carried(self):
        stt = {"legacy_v3": ["آباد"], "last_rebalance_session": 20250195,
               "war2": {"stops": {"نماد4": {"status": "pending", "trigger_session": 20250198, "cycle": 20250195}}}}
        p = self._plan(state=stt, force=False)
        self.assertEqual(sorted((x["symbol"], x["status"]) for x in p["stops"]), [("نماد3", "new"), ("نماد4", "pending")])
        self.assertEqual({r["symbol"]: r["status"] for r in p["risk"]}["نماد4"], "pending")

    def test_off_means_pure_f3(self):
        p = self._plan(cfg=dict(LIVE, stop_fixed_pct=0))
        self.assertFalse(p["war2"]["active"])
        self.assertNotIn("stops", p); self.assertNotIn("risk", p)
        self.assertTrue(all("stop_price_est" not in b for b in p["buys"]))
        self.assertNotIn("stopped_this_cycle", p)

    def test_main_out_touches_no_state(self):
        def f(st):
            with mock.patch.object(LV, "build_plan", lambda force=False: {"action": "hold", "buys": [], "sells": [],
                                                                          "notes": [], "war2_basis": {"x": {}}}), \
                    mock.patch.object(LV, "PLAN", st / "fund_plan.json"), mock.patch.object(LV, "STATE", st), \
                    mock.patch.object(LV, "LOG", st / "fund_log.jsonl"):
                LV.main(["--plan", "--out", str(st / "rehearsal.json")])
                a = (st / "rehearsal.json").exists(), (st / "fund_plan.json").exists(), FL.load_state().get("war2")
                LV.main(["--plan"])
                return a, FL.load_state().get("war2", {}).get("basis")
        (a, b) = run_in(f, LIVE)
        self.assertEqual(a, (True, False, None))
        self.assertEqual(b, {"x": {}})


def oplan(cash, positions, state, plan, cfg=LIVE, **kw):
    return run_in(lambda st: FO.plan_now(cash, kw.pop("open_orders", []), sold_afran_today=True,
                                         bought_today=set(), **PX, **kw), cfg, positions=positions, state=state, plan=plan)


BASEPOS = {"افران": (13000, 57574.0, 56838.0), "آباد": (629746, 2595.0, 2657.0)}
COMMITTED = {"legacy_v3": ["آباد"], "last_rebalance_session": 20261125, "target": TEN}
HOLD_PLAN = {"at": NOW, "action": "hold", "session": 20261128, "target": [], "stops": []}


class Orders(unittest.TestCase):
    def test_new_stop_sold_first_whole_qty(self):
        pos = dict(BASEPOS, **{"الف": (150000, 850.0, 1000.0)})
        plan = dict(HOLD_PLAN, stops=[{"symbol": "الف", "status": "new", "trigger_session": 20261128, "close": 850.0,
                                       "stop_price": 853.2, "cycle": 20261125, "qty": 150000}])
        p = oplan(0, pos, COMMITTED, plan)
        self.assertEqual([(s["symbol"], s["qty"], s["reason"]) for s in p["stop_sells"]], [("الف", 150000, "war2_stop")])
        self.assertEqual(p["stop_sells"][0]["price"], int(850 * 0.97))
        self.assertIn("الف", p["stopped_this_cycle"])

    def test_floor_lock_retry_and_open_order(self):
        st = dict(COMMITTED, war2={"stops": {"الف": {"status": "pending", "trigger_session": 20261128, "close": 850.0,
                                                    "cycle": 20261125, "qty": 150000}}})
        pos = dict(BASEPOS, **{"الف": (150000, 830.0, 1000.0)})
        stale = dict(HOLD_PLAN, at=(datetime.now() - timedelta(days=9)).strftime("%Y-%m-%d %H:%M:%S"))
        p = oplan(0, pos, st, stale)                             # next day, unfilled (floor queue): placed again
        self.assertEqual([s["symbol"] for s in p["stop_sells"]], ["الف"])
        p = oplan(0, pos, st, stale, open_orders=[{"symbol": "الف", "side": "فروش", "qty": 150000, "price": 820}])
        self.assertEqual(p["stop_sells"], [])                   # its order is still in the queue: no second order
        pos2 = dict(BASEPOS, **{"الف": (40000, 830.0, 1000.0)})   # partial fill -> the rest
        self.assertEqual(oplan(0, pos2, st, stale)["stop_sells"][0]["qty"], 40000)
        p = oplan(0, BASEPOS, st, stale)                         # gone at the broker -> sold, nothing to send
        self.assertEqual(p["stop_sells"], [])
        self.assertEqual(p["stops"][0]["status"], "sold")

    def test_no_rebuy_in_cycle_cash_waits(self):
        st = dict(COMMITTED, war2={"stops": {"الف": {"status": "sold", "trigger_session": 20261128, "close": 850.0,
                                                    "cycle": 20261125, "qty": 150000}}})
        held = {s: (150000, 1000.0, 1000.0) for s in TEN[1:]}
        p = oplan(3e9, dict(BASEPOS, **held), st, HOLD_PLAN)
        self.assertNotIn("الف", [b["symbol"] for b in p["buys"]])
        self.assertTrue(any("War2 stopped this cycle" in n for n in p["notes"]))
        self.assertAlmostEqual(p["cash_reserved_for_stopped"], min(p["per_name"], 150000 * 850.0), delta=1)
        self.assertLessEqual(p["spendable"], 3e9 * 0.995 - p["cash_reserved_for_stopped"] + 1)
        # next rebalance: the stopped name may come back through the normal selection
        newplan = {"at": NOW, "action": "rebalance", "session": 20261227, "target": TEN,
                   "ranked_top": [{"sym": s, "close": 1000.0} for s in TEN], "stops": []}
        p = oplan(3e9, dict(BASEPOS, **held), st, newplan)
        self.assertTrue(p["new_rebalance"])
        self.assertIn("الف", [b["symbol"] for b in p["buys"]])
        self.assertNotIn("cash_reserved_for_stopped", p)

    def test_pending_never_topped_up_or_rebalance_sold_twice(self):
        st = dict(COMMITTED, war2={"stops": {"الف": {"status": "pending", "trigger_session": 20261128,
                                                    "close": 850.0, "cycle": 20261125}}})
        newplan = {"at": NOW, "action": "rebalance", "session": 20261227, "target": TEN[1:],
                   "ranked_top": [{"sym": s, "close": 1000.0} for s in TEN], "stops": []}
        pos = dict(BASEPOS, **{"الف": (10000, 850.0, 1000.0)})
        p = oplan(3e9, pos, st, newplan)
        self.assertEqual([s["symbol"] for s in p["stop_sells"]], ["الف"])
        self.assertEqual(p["sells"], [])                         # not also a rebalance sell
        self.assertNotIn("الف", [b["symbol"] for b in p["buys"]])

    def test_manual_and_legacy_never_stopped(self):
        plan = dict(HOLD_PLAN, stops=[{"symbol": s, "status": "new", "close": 1.0, "trigger_session": 1}
                                      for s in ("افران", "خساپا", "آباد")])
        p = oplan(0, dict(BASEPOS, **{"خساپا": (100, 674.0, 149.0)}), COMMITTED, plan)
        self.assertEqual(p["stop_sells"], [])

    def test_off_no_stop_orders(self):
        plan = dict(HOLD_PLAN, stops=[{"symbol": "الف", "status": "new", "close": 850.0}])
        p = oplan(0, dict(BASEPOS, **{"الف": (150000, 850.0, 1000.0)}), COMMITTED, plan,
                  cfg=dict(LIVE, stop_fixed_pct=0))
        self.assertEqual(p["stop_sells"], [])
        self.assertFalse(p["war2"]["active"])


class RunOrder(unittest.TestCase):
    def test_stop_sell_goes_first_and_is_recorded(self):
        sent = []
        oc = types.ModuleType("order_candidates")
        oc._free_balance = lambda: 0.0
        oc._open_orders = lambda: []
        pos = {"افران": (39272, 57574.0, 56838.0), "آباد": (629746, 2595.0, 2657.0), "الف": (150000, 850.0, 1000.0)}
        plan = dict(HOLD_PLAN, stops=[{"symbol": "الف", "status": "new", "trigger_session": 20261128, "close": 850.0,
                                       "stop_price": 853.2, "cycle": 20261125, "qty": 150000}])

        def flow(symbol, isin, qty, price, side, dry):
            sent.append((side, symbol)); return {"submitted": True, "message": "ok", "placed_orderid": "9"}

        def f(st):
            with mock.patch.dict(sys.modules, {"order_candidates": oc}), mock.patch.object(FO, "_flow", flow), \
                    mock.patch.object(FO, "_isin", lambda s: "IR"), mock.patch.object(FO, "_stop_px", PX["stop_px"]), \
                    mock.patch.object(FO, "_sell_px", PX["sell_px"]), mock.patch.object(FO, "time") as t:
                t.time.return_value = 0; t.sleep = lambda s: None
                FO.run(force=True)
                first = json.loads((st / "fund_state.json").read_text(encoding="utf-8")), list(sent)
                sent.clear()
                FO.run(force=True)                    # same session again: open-order lag guard, no 2nd order
                return first + (list(sent),)
        st, first_sent, second_sent = run_in(f, LIVE, positions=pos, state=dict(COMMITTED), plan=plan)
        self.assertEqual(first_sent[0], ("sell", "الف"))                 # the stop goes before everything
        self.assertNotIn(("sell", "الف"), second_sent)
        v = st["war2"]["stops"]["الف"]
        self.assertEqual((v["status"], v["orders"], v["cycle"]), ("pending", 1, 20261125))


class V3ExitsOff(unittest.TestCase):
    def test_exit_engine_judges_only_legacy(self):
        import exit_engine as EE
        rows = [{"symbol": "الف", "qty": 10, "avg_cost": 1000}, {"symbol": "آباد", "qty": 10, "avg_cost": 3000},
                {"symbol": "افران", "qty": 10, "avg_cost": 90000}]
        seen = {}

        def plan(positions, *a, **k):
            seen["syms"] = [p["symbol"] for p in positions]; return []

        def f(st):
            with mock.patch.object(EE, "fresh_panel_positions", lambda rebuild=True: (rows, {})), \
                    mock.patch.object(EE, "cross_checked_prices", lambda s, p: ({}, {}, {})), \
                    mock.patch.object(EE, "read_open_orders", lambda: []), mock.patch.object(EE, "plan", plan), \
                    mock.patch.object(EE, "holiday_set", lambda: set()), mock.patch.object(EE, "config", lambda: {}):
                EE.prepare_cycle()
        run_in(f, LIVE, positions={"الف": (10, 900.0, 1000.0), "آباد": (10, 2595.0, 3000.0)})
        self.assertEqual(seen["syms"], ["آباد"])

    def test_sell_monitor_skips_fund(self):
        try:
            import sell_monitor as SM
        except Exception as exc:          # pandas etc. not installed in the sandbox
            self.skipTest(str(exc)); return
        calls = []

        def f(st):
            with mock.patch.object(SM, "positions", lambda: [{"symbol": "الف", "qty": 10, "avg_cost": 1000.0},
                                                             {"symbol": "افران", "qty": 1, "avg_cost": 1.0}]), \
                    mock.patch.object(SM, "_targets", lambda: {}), \
                    mock.patch.object(SM, "_last_price", lambda s: calls.append(s) or 1.0), \
                    mock.patch.object(SM, "REVIEW", st / "rv.json"), mock.patch.object(SM, "CAND", st / "c.csv"):
                return SM.run(place_orders=False)
        out = run_in(f, LIVE, positions={"الف": (10, 900.0, 1000.0)})
        self.assertEqual(out["positions"], []); self.assertEqual(calls, [])


class StateHelpers(unittest.TestCase):
    def test_merge_mark_roll(self):
        st = {"last_rebalance_session": 1}
        W2.merge_plan_stops(st, [{"symbol": "x", "trigger_session": 5, "cycle": 1}], "t")
        W2.merge_plan_stops(st, [{"symbol": "x", "trigger_session": 5, "cycle": 1}], "t")   # idempotent
        self.assertEqual(list(W2.pending(st)), ["x"])
        self.assertEqual(W2.mark_sold(st, {"y"}, "t2"), ["x"])
        W2.merge_plan_stops(st, [{"symbol": "x", "trigger_session": 5, "cycle": 1}], "t3")  # stale plan re-read
        self.assertEqual(st["war2"]["stops"]["x"]["status"], "sold")
        self.assertEqual(W2.blocked(st, [], False), {"x"})
        self.assertEqual(W2.blocked(st, [], True), set())
        W2.roll_cycle(st, 2)
        self.assertEqual(st["war2"]["stops"], {})
        self.assertEqual(st["war2"]["history"][-1]["symbol"], "x")


if __name__ == "__main__":
    unittest.main()
