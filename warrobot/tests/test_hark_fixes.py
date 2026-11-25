"""Unit tests / simulations for the three Hark fixes (no browser, no broker).

    cd warrobot && python -m unittest tests.test_hark_fixes -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import exit_engine as ee  # noqa: E402
import morning_orders as mo  # noqa: E402

TEHRAN = ZoneInfo("Asia/Tehran")


class FakeClock:
    """OS clock that only moves when somebody sleeps (deterministic)."""

    def __init__(self, t: float):
        self.t = t
        self.sleeps = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


# ============================================================ bug 3: exit engine
class ExitDecisionTests(unittest.TestCase):
    TODAY = "2026-11-25"

    def lot(self, qty, buy, at="2026-11-20 21:00:00"):
        return {"qty": qty, "buy": buy, "cost": buy * (1 + ee.LOT_FEE_PCT / 100), "at": at}

    def test_lot_take_profit_although_blended_average_is_low(self):
        # owner's case: first lot +25 %, blended average only ~+9 %
        pos = {"symbol": "آواپارس", "qty": 152251, "avg_cost": 7736}
        lots = [self.lot(82542, 6855), self.lot(69709, 8600)]
        d = ee.evaluate(pos, lots, 8650, 25, 3, self.TODAY)
        self.assertEqual(d.action, "sell")
        self.assertEqual(d.reason, "take_profit")
        self.assertEqual(d.due_qty, 82542)

    def test_stop_loss_on_lot_cost(self):
        pos = {"symbol": "آباد", "qty": 629746, "avg_cost": 2680}
        d = ee.evaluate(pos, [self.lot(629746, 2657)], 2094, 25, 3, self.TODAY)
        self.assertEqual((d.action, d.reason, d.due_qty), ("sell", "stop_loss", 629746))

    def test_hold_inside_band(self):
        pos = {"symbol": "آباد", "qty": 1000, "avg_cost": 2680}
        d = ee.evaluate(pos, [self.lot(1000, 2657)], 2650, 25, 3, self.TODAY)
        self.assertEqual(d.action, "hold")

    def test_fresh_lot_bought_today_is_not_judged(self):
        pos = {"symbol": "آردینه", "qty": 1000, "avg_cost": 52000}
        d = ee.evaluate(pos, [self.lot(1000, 52000, at=self.TODAY + " 08:45:01")], 49000, 25, 3,
                        self.TODAY)
        self.assertEqual(d.action, "hold")

    def test_unjournaled_shares_use_broker_average(self):
        pos = {"symbol": "افرا", "qty": 5000, "avg_cost": 1000}
        d = ee.evaluate(pos, [], 960, 25, 3, self.TODAY)
        self.assertEqual((d.action, d.reason, d.due_qty), ("sell", "stop_loss", 5000))

    def test_manual_only_and_missing_price(self):
        self.assertEqual(ee.evaluate({"symbol": "افران", "qty": 10, "avg_cost": 1}, [], 99, 25, 3,
                                     self.TODAY).reason, "manual_only")
        self.assertEqual(ee.evaluate({"symbol": "آباد", "qty": 10, "avg_cost": 1}, [], 0, 25, 3,
                                     self.TODAY).reason, "no_live_price")

    def test_sell_limit_rules(self):
        m_bids = {"floor": 2031, "ceiling": 2157, "bids": [(2090, 10)], "asks": []}
        self.assertEqual(ee.sell_limit("stop_loss", 2094, m_bids), (2090, "best_bid"))
        m_locked = {"floor": 2031, "ceiling": 2157, "bids": [], "asks": [(2031, 10 ** 7)]}
        self.assertEqual(ee.sell_limit("stop_loss", 2094, m_locked), (2031, "floor"))
        self.assertEqual(ee.sell_limit("take_profit", 2100, m_locked)[0], 2100)
        # stale 2,595 snapshot price must never survive: clamped into today's band
        self.assertEqual(ee.sell_limit("take_profit", 2595, m_locked)[0], 2157)


class ExitPlanTests(unittest.TestCase):
    TODAY = "2026-11-25"
    CFG = {"tp": 25.0, "sl": 3.0, "reprice_after_min": 10}
    POS = [{"symbol": "آباد", "qty": 629746, "avg_cost": 2680, "isin": "IRO1ABAD0001"}]
    LOTS = {"آباد": [{"qty": 629746, "buy": 2657, "cost": 2657 * 1.0089, "at": "2026-11-22 22:12:54"}]}
    MKT = {"آباد": {"floor": 2031, "ceiling": 2157, "bids": [], "asks": [(2031, 1)]}}

    def test_sells_full_due_qty_at_floor(self):
        d = ee.plan(self.POS, self.LOTS, {"آباد": 2094}, self.MKT, [], self.CFG, self.TODAY,
                    recent_qty=lambda s: 0)[0]
        self.assertEqual((d.action, d.qty, d.order_price), ("sell", 629746, 2031))

    def test_no_duplicate_when_sell_already_open(self):
        oo = [{"symbol": "آباد", "side": "فروش", "qty": 629746, "executed": 0}]
        d = ee.plan(self.POS, self.LOTS, {"آباد": 2094}, self.MKT, oo, self.CFG, self.TODAY,
                    recent_qty=lambda s: 0)[0]
        self.assertEqual((d.action, d.note), ("skip", "sell_order_already_open"))

    def test_remaining_qty_after_ticket_cap(self):
        oo = [{"symbol": "آباد", "side": "فروش", "qty": 300000, "executed": 0}]
        d = ee.plan(self.POS, self.LOTS, {"آباد": 2094}, self.MKT, oo, self.CFG, self.TODAY,
                    recent_qty=lambda s: 300000)[0]
        self.assertEqual(d.qty, 329746)

    def test_just_sent_order_not_yet_visible_is_not_resent(self):
        d = ee.plan(self.POS, self.LOTS, {"آباد": 2094}, self.MKT, [], self.CFG, self.TODAY,
                    recent_qty=lambda s: 629746)[0]
        self.assertEqual(d.action, "skip")

    def test_unknown_open_orders_fail_safe(self):
        d = ee.plan(self.POS, self.LOTS, {"آباد": 2094}, self.MKT, None, self.CFG, self.TODAY,
                    recent_qty=lambda s: 0)[0]
        self.assertEqual((d.action, d.note), ("skip", "open_orders_unknown"))

    def test_execute_logs_and_uses_flow_submitted_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = (ee.EXITS_LOG, ee.EXIT_ORDERS)
            ee.EXITS_LOG, ee.EXIT_ORDERS = Path(tmp) / "exits.jsonl", Path(tmp) / "orders.json"
            sys.modules["trade_journal"] = types.SimpleNamespace(record_sell=lambda *a, **k: None)
            sys.modules["profit_transfer"] = types.SimpleNamespace(transfer=lambda *a, **k: {"ok": 1})
            calls = []

            def sender(sym, isin, qty, price, at_epoch_ms=0, priority=False):
                calls.append((sym, isin, qty, price, at_epoch_ms))
                return {"submitted": True, "message": "ثبت شد (تأیید از سفارشات باز)",
                        "placed_orderid": "3026", "qty": qty}
            try:
                decs = ee.plan(self.POS, self.LOTS, {"آباد": 2094}, self.MKT, [], self.CFG,
                               self.TODAY, recent_qty=lambda s: 0)
                res = ee.execute(decs, self.POS, "intraday", sender=sender, at_epoch_ms=123)
                self.assertEqual(calls, [("آباد", "IRO1ABAD0001", 629746, 2031, 123)])
                self.assertTrue(res[0]["submitted"])
                self.assertEqual(ee.recent_submitted_qty("آباد"), 629746)
                logged = [json.loads(x) for x in ee.EXITS_LOG.read_text(encoding="utf-8").splitlines()]
                self.assertEqual(logged[0]["reason"], "intraday_stop_loss")
                # second cycle right after: nothing is sent again
                decs2 = ee.plan(self.POS, self.LOTS, {"آباد": 2094}, self.MKT, [], self.CFG, self.TODAY)
                self.assertEqual(decs2[0].action, "skip")
            finally:
                ee.EXITS_LOG, ee.EXIT_ORDERS = old

    def test_stale_stop_is_repriced_only_if_ours_and_old(self):
        d = ee.Decision(symbol="آباد", action="skip", reason="stop_loss", live_price=2094,
                        note="sell_order_already_open")
        oo = [{"symbol": "آباد", "side": "فروش", "orderid": "1", "price": 2595},
              {"symbol": "آباد", "side": "فروش", "orderid": "2", "price": 2595}]   # 2 = operator's
        book = {"orders": [{"orderid": "1", "ts": time.time() - 15 * 60}]}
        st = ee.stale_stop_orders([d], oo, self.MKT, self.CFG, book=book)
        self.assertEqual([s["orderid"] for s in st], ["1"])


class OldPriceBugRegression(unittest.TestCase):
    def test_intraday_live_price_uses_newest_daily_row(self):
        rows = [{"dEven": 20261124, "pClosing": 2094}, {"dEven": 20260810, "pClosing": 4370}]
        sys.modules["tsetmc"] = types.SimpleNamespace(
            resolve=lambda s: {"ins_code": "1"},
            closing_daily=lambda c: {"closingPriceDaily": rows})
        sys.modules.setdefault("pw_lock", __import__("pw_lock"))
        import intraday_exit
        self.assertEqual(intraday_exit.live_price("آباد"), 2094)


# ================================================= bug 1: precise pre-open send
class MorningTimingTests(unittest.TestCase):
    def test_os_epoch_corrects_clock_offset(self):
        target = datetime(2026, 11, 25, 8, 45, 1, tzinfo=TEHRAN)
        self.assertEqual(mo.os_epoch_for(target, 1800), target.timestamp() - 1800)
        self.assertEqual(mo.os_epoch_for(target, 0), target.timestamp())

    def test_sleep_until_is_precise(self):
        clk = FakeClock(1000.0)
        mo.sleep_until(1012.345, clk.now, clk.sleep)
        self.assertGreaterEqual(clk.t, 1012.345)
        self.assertLess(clk.t - 1012.345, 0.01)

    def test_first_order_clicks_at_target_rest_follow(self):
        target = datetime(2026, 11, 25, 8, 45, 1, tzinfo=TEHRAN).timestamp()
        deadline = datetime(2026, 11, 25, 8, 59, 30, tzinfo=TEHRAN).timestamp()
        clk = FakeClock(target - 200)          # prepare finished at ~08:41:41
        sent = []

        def flow(kind):
            def f(item, at_ms):
                start = clk.t
                if at_ms:                        # flow.js: bind+fill (12 s) then waits to the ms
                    clk.t += 12
                    self.assertLess(clk.t, at_ms / 1000)
                    clk.t = at_ms / 1000
                else:
                    clk.t += 15
                sent.append((kind, at_ms, start))
                return {"submitted": True, "click_epoch_ms": int(clk.t * 1000)}
            return f
        q = [{"side": "sell", "decision": object()}, {"side": "buy", "rec": {}}, {"side": "buy", "rec": {}}]
        res = mo.run_queue(q, target, deadline, flow("sell"), flow("buy"), clk.now, clk.sleep)
        self.assertEqual(sent[0][1], int(target * 1000))
        self.assertAlmostEqual(sent[0][2], target - mo.START_LEAD_SEC, delta=0.01)
        self.assertEqual(res[0]["click_lag_ms"], 0)
        self.assertEqual([s[1] for s in sent[1:]], [0, 0])

    def test_nothing_after_deadline(self):
        clk = FakeClock(2000.0)
        res = mo.run_queue([{"side": "buy", "rec": {}}], 1900.0, 1950.0,
                           lambda *a: {}, lambda *a: {"submitted": True}, clk.now, clk.sleep)
        self.assertIn("skipped", res[0])

    def test_queue_never_sends_unsized_buy(self):
        plan = {"planned": [{"symbol": "a", "qty": 0, "price": 10}, {"symbol": "b", "qty": 5, "price": 10}]}
        q = mo.build_queue([], plan, "sell")
        self.assertEqual([x["rec"]["symbol"] for x in q], ["b"])


class MorningMainSimulation(unittest.TestCase):
    """morning_orders.main() end to end with every broker touch stubbed."""

    def test_main_targets_offset_corrected_second_and_uses_priority(self):
        import contextlib
        with tempfile.TemporaryDirectory() as tmp:
            sent = []
            clock = types.ModuleType("tsebot.clock")
            clock.calibrate = lambda **k: {"stored": 1800, "measured": 1800}
            clock.current_offset = lambda: 1800.0          # bot clock = OS + 30 min
            tsebot = types.ModuleType("tsebot")
            tsebot.clock = clock
            dec = ee.Decision(symbol="آباد", action="sell", reason="stop_loss", qty=10, order_price=2031)
            stubs = {
                "tsebot": tsebot, "tsebot.clock": clock,
                "market_cal": types.SimpleNamespace(market_day=lambda *a: True),
                "session_keeper": types.SimpleNamespace(check=lambda **k: {"ok": True, "state": "alive"}),
                "pw_lock": types.SimpleNamespace(guard=lambda *a, **k: contextlib.nullcontext(True)),
                "preopen_cleanup": types.SimpleNamespace(main=lambda: 0),
                "exit_engine": types.SimpleNamespace(
                    prepare_cycle=lambda **k: {"decisions": [dec], "positions": []},
                    execute=lambda decs, pos, tag, **k: [sent.append(("sell", k["at_epoch_ms"],
                                                                      os.environ.get("WARROBOT_UI_PRIORITY")))
                                                         or {"submitted": True}]),
                "order_candidates": types.SimpleNamespace(
                    prepare=lambda: {"planned": [{"symbol": "آردینه", "qty": 5, "price": 50000}]},
                    execute=lambda plan, **k: sent.append(("buy", k["at_epoch_ms"], None))
                    or {"orders": [{"submitted": True}]}),
            }
            saved = {k: sys.modules.get(k) for k in stubs}
            sys.modules.update(stubs)
            old = (mo.STATE, mo.OUT, mo.PRIORITY, mo.START_LEAD_SEC, mo.DEADLINE)
            mo.STATE, mo.OUT, mo.PRIORITY = Path(tmp), Path(tmp) / "m.json", Path(tmp) / "ui_priority"
            mo.START_LEAD_SEC = 400
            from datetime import timedelta, time as dtime
            bot_now = datetime.fromtimestamp(time.time() + 1800, TEHRAN)
            at = (bot_now + timedelta(seconds=120)).replace(microsecond=0)
            mo.DEADLINE = dtime(23, 59, 59) if at.time() < dtime(23, 59) else dtime(23, 59, 59, 999999)
            try:
                if at.date() != bot_now.date():
                    self.skipTest("too close to midnight")
                rc = mo.main(["--at", at.strftime("%H:%M:%S")])
                self.assertEqual(rc, 0)
                expected_ms = int((at.timestamp() - 1800) * 1000)
                self.assertEqual(sent[0][0], "sell")
                self.assertEqual(sent[0][1], expected_ms, "first order clicks at the corrected second")
                self.assertEqual(sent[0][2], "1", "children pass our own UI priority")
                self.assertEqual(sent[1][:2], ("buy", 0))
                self.assertFalse(mo.PRIORITY.exists(), "priority released")
                rep = json.loads(mo.OUT.read_text(encoding="utf-8"))
                self.assertEqual(rep["submitted"], 2)
            finally:
                mo.STATE, mo.OUT, mo.PRIORITY, mo.START_LEAD_SEC, mo.DEADLINE = old
                os.environ.pop("WARROBOT_UI_PRIORITY", None)
                for k, v in saved.items():
                    if v is None:
                        sys.modules.pop(k, None)
                    else:
                        sys.modules[k] = v


class OrderCandidatesExecuteTests(unittest.TestCase):
    def test_execute_first_only_waits_and_is_idempotent(self):
        import order_candidates as oc
        with tempfile.TemporaryDirectory() as tmp:
            old = oc.LOG
            oc.LOG = Path(tmp) / "orders_log.jsonl"
            sys.modules["trade_journal"] = types.SimpleNamespace(record_buy=lambda *a, **k: None)
            seen = []

            def sender(rec, at_epoch_ms=0, priority=False):
                seen.append((rec["symbol"], at_epoch_ms))
                # flow.js verified it in the open-order list but the message is the
                # generic one: the old regex made this submitted=False
                return {"submitted": True, "message": "no_order_response", "qty": rec["qty"],
                        "placed_orderid": "9"}
            try:
                plan = {"planned": [{"symbol": "آباد", "isin": "X", "qty": 10, "price": 2000},
                                    {"symbol": "آردینه", "isin": "Y", "qty": 0, "price": 0},
                                    {"symbol": "آرمان", "isin": "Z", "qty": 5, "price": 1000}]}
                out = oc.execute(plan, at_epoch_ms=777, sender=sender)
                self.assertEqual(seen, [("آباد", 777), ("آرمان", 0)])
                self.assertEqual(out["submitted"], 2)
                self.assertEqual(out["orders"][1]["skipped"], "qty_zero_unpriced")
                out2 = oc.execute(plan, sender=sender)
                self.assertEqual(len(seen), 2)               # nothing sent twice
                self.assertEqual(out2["orders"][0]["skipped"], "already_submitted_today")
            finally:
                oc.LOG = old


# ============================================== scheduler: never block, one owner
class SchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import scheduler
        cls.s = scheduler

    def states(self, today, hm, trading=True):
        return {j: st for j, _, st in self.s.due_jobs(today, hm, trading)}

    def test_single_owner_of_the_pre_open(self):
        names = [e[1] for e in self.s.JOBS]
        for gone in ("pwreset", "loginsync", "lotsell", "preexit", "order", "preclean"):
            self.assertNotIn(gone, names)
        self.assertEqual(len(names), len(set(names)), "duplicate job names never run twice")

    def test_morning_fires_at_0841(self):
        today = {j: "x" for _, j, *_ in self.s.JOBS if j not in ("morning",)}
        self.assertEqual(self.states(today, "08:41").get("morning"), "fire")

    def test_after_morning_jobs_wait(self):
        today = {"morning": "08:41"}
        st = self.states(today, "08:46")
        self.assertEqual(st["ordsup"], "wait")
        self.assertEqual(st["ready45"], "wait")
        today["morning#done"] = "08:46:30"
        self.assertEqual(self.states(today, "08:47")["ordsup"], "fire")

    def test_browser_checks_never_run_into_the_order(self):
        st = self.states({}, "08:42")
        for j in ("ready40", "preflight", "preready", "ready30"):
            self.assertEqual(st[j], "skipped")

    def test_quiet_window_keeps_session_keeper(self):
        jobs = {j for j, _, _ in self.s.due_repeats({}, "08:44", True)}
        self.assertIn("session", jobs)
        self.assertNotIn("d_balance_night", jobs)
        self.assertIn("exitwatch", {j for j, _, _ in self.s.due_repeats({}, "09:06", True)})

    def test_jobs_start_in_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.s.JOBLOGS = Path(tmp)
            script = Path(tmp) / "slow.py"
            script.write_text("import time; time.sleep(3); print('done')", encoding="utf-8")
            old_base = self.s.BASE
            self.s.BASE = Path(tmp)
            try:
                t0 = time.time()
                self.assertTrue(self.s._start("slow", "slow.py"))
                self.assertLess(time.time() - t0, 1.0, "the loop must not wait for the job")
                self.assertFalse(self.s._start("slow", "slow.py"), "never stacked")
                data = {}
                while "slow" in self.s.RUNNING and time.time() - t0 < 10:
                    self.s._reap(data, "d")
                    time.sleep(0.2)
                self.assertIn("slow#done", data["d"])
            finally:
                self.s.BASE = old_base


# ======================================================== bug 2: lock + sessions
class PwLockTests(unittest.TestCase):
    def setUp(self):
        import pw_lock
        self.pl = pw_lock
        self.tmp = tempfile.TemporaryDirectory()
        self.old = pw_lock.LOCK
        pw_lock.LOCK = Path(self.tmp.name) / "pw.lock"

    def tearDown(self):
        self.pl.LOCK = self.old
        self.tmp.cleanup()

    def test_dead_owner_is_replaced(self):
        self.pl.LOCK.write_text(f"999999|{time.time()}|x", encoding="utf-8")
        with self.pl.guard("t", wait=3) as got:
            self.assertTrue(got)
        self.assertFalse(self.pl.LOCK.exists())

    def test_live_owner_is_not_stolen_after_240s(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.pl.LOCK.write_text(f"{p.pid}|{time.time() - 300}|order", encoding="utf-8")
            with self.pl.guard("t", wait=1.5) as got:
                self.assertFalse(got, "a 5-minute order run must keep its lock")
            self.assertTrue(self.pl.LOCK.exists())
        finally:
            p.kill()

    def test_reentrant_does_not_release_outer(self):
        with self.pl.guard("outer", wait=2) as a:
            with self.pl.guard("inner", wait=2) as b:
                self.assertTrue(a and b)
            self.assertTrue(self.pl.LOCK.exists())


class PwResetTests(unittest.TestCase):
    def test_shared_browser_is_not_killed(self):
        import pw_reset
        old = pw_reset._shared_browser_alive
        pw_reset._shared_browser_alive = lambda: True
        try:
            self.assertEqual(pw_reset.kill_orphans(), 0)
        finally:
            pw_reset._shared_browser_alive = old


class SmtpSecretTests(unittest.TestCase):
    def test_password_comes_from_env(self):
        root = HERE.parent / "tese-robot"
        cfg = json.loads((root / "config" / "email.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["app_password"], "")
        code = ("import os,sys; sys.path.insert(0,'src'); os.environ['WARROBOT_SMTP_PASSWORD']='abc';"
                "import importlib; from tsebot import alerts; print(alerts._load_cfg()['app_password'])")
        p = subprocess.run([sys.executable, "-c", code], cwd=str(root), capture_output=True, text=True)
        if p.returncode != 0 and "No module named" in p.stderr:
            self.skipTest("tsebot dependencies missing: " + p.stderr.strip().splitlines()[-1])
        self.assertEqual(p.stdout.strip(), "abc", p.stderr)


class JsSharedBrowserTests(unittest.TestCase):
    def test_cdp_shared_shim(self):
        node = "node"
        try:
            subprocess.run([node, "--version"], capture_output=True, check=True)
        except Exception:
            self.skipTest("node not installed")
        p = subprocess.run([node, str(HERE / "tests" / "js" / "test_cdp_shared.js")],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
