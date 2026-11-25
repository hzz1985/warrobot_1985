# -*- coding: utf-8 -*-
"""TSE Bot v2 — single-process scheduler.

One clock, one place that decides what may run when. v1 had three schedulers
(Task Scheduler wrappers, bourse_ml slots, and inner loops) that disagreed, which
is how `pm_loop` started at 09:00 and immediately exited because its own window
began at 09:05.

Daily timeline (Tehran):
  08:20  preflight   connect, sync fills, reconcile, load picks
  08:40  pre-open    build the opening-auction plan (buys AND exits)
  08:30+ sell loop   pre-open auction through the closing auction
  08:45+ buy rounds   at buy.attempts_tehran
  13:10  close report
  20:30  nightly data refresh (TSE -> daily CSVs)
  21:00  build the day's intraday/Burst features
  21:20  generate queue_v3 picks for tomorrow

Tasks are idempotent per day via `state/ran.json`; a restart does not repeat them.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import traceback
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Callable, Optional

from .calendar_ir import _t, now_tehran
from .config import ROOT, Config
from .engine import Engine
from .state import JsonState


def _hm(ts: datetime) -> str:
    return ts.strftime("%H:%M")


class Scheduler:
    def __init__(self, cfg: Config, engine: Engine, log: Optional[Callable[[str], None]] = None):
        self.cfg = cfg
        self.engine = engine
        self.state = JsonState(cfg.path("state_dir") / "ran.json")
        self.log = log or (lambda m: None)
        self._last_sell = 0.0
        self._last_buy = 0.0
        self._last_sync = 0.0
        self._preflight_try = 0.0
        self._login_watch_try = 0.0
        self._keepalive_try = 0.0

    # ------------------------------------------------------------------ tasks
    def _unmark(self, day: str, key: str) -> None:
        ran = dict(self.state.get(day) or {})
        if key in ran:
            ran.pop(key, None)
            self.state.set(day, ran)

    def _once(self, day: str, key: str) -> bool:
        ran = dict(self.state.get(day) or {})
        if ran.get(key):
            return False
        ran[key] = now_tehran().isoformat(timespec="seconds")
        self.state.set(day, ran)
        return True

    def task_preflight(self) -> None:
        self.log("preflight: connecting")
        try:
            self.engine.connect()
        except Exception as exc:
            # Do not burn the once-a-day mark: the operator may log in at 08:25.
            self._unmark(self.engine.today(), "preflight")
            self.log(f"preflight: connect failed ({exc}); will retry next tick")
            raise
        sync = self.engine.sync()
        self.log(f"preflight: sync={sync}")
        snap = self.engine.account_snapshot()
        self.log(f"preflight: account={snap}")
        if sync.get("diffs"):
            self.log(f"ALERT reconcile mismatch: {sync['diffs']}")
        try:
            picks = self.engine.load_picks()
            self.log(f"preflight: picks={[(p.symbol, round(p.score, 3)) for p in picks]}")
        except Exception as exc:
            self.log(f"preflight: picks error {exc}")

    def task_preopen_plan(self) -> None:
        """Decide, before 09:00, what to buy and what to exit at the open.

        This is the answer to the structural constraint: an order that is not in
        the book before the opening match cannot participate in it.
        """
        self.engine.connect()
        self.engine.sync()
        marks = self.engine.refresh_marks()
        exits = [d.__dict__ for d in self.engine.scan_exits()]
        try:
            picks = self.engine.load_picks()
            pick_rows = [{"symbol": p.symbol, "score": round(p.score, 6)} for p in picks]
        except Exception as exc:
            pick_rows = [{"error": str(exc)}]
        plan = {
            "at": now_tehran().isoformat(timespec="seconds"),
            "session": self.engine.session(),
            "marks": marks,
            "exits": exits,
            "picks": pick_rows,
            "note": "orders must be entered during pre-open to join the opening match",
        }
        out = self.cfg.path("picks_dir") / "preopen_plan.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        self.log(f"preopen plan -> {out} exits={len(exits)} picks={len(pick_rows)}")

    def task_login_watch(self) -> None:
        """Early warning: the broker session is what gates every order."""
        from .alerts import alert_login_required
        url = ""
        try:
            self.engine.broker.connect()
            url = str(getattr(self.engine.broker.driver, "current_url", ""))
            if self.engine.broker.is_logged_in():
                self.log("login watch: session OK")
                return
        except Exception as exc:
            self.log(f"login watch: connect failed {exc}")
        self.log("login watch: NOT LOGGED IN -> email reminder")
        alert_login_required(url)

    def task_keepalive(self) -> None:
        """Reset both the client-side idle timer and the server session.

        A plain XHR was not enough: broker panels log out when the page sees no
        user events. `broker.keepalive()` dispatches a real mouse event through
        CDP and then pings, so the SPA's idle counter restarts without a reload.
        """
        if float(self.cfg.get("schedule.keepalive_sec", 25) or 0) <= 0:
            return  # keepalive disabled by config
        try:
            try:
                self.engine.connect()
            except Exception as exc:
                self.log(f"keepalive: connect failed ({exc})")
                return
            if not self.engine.broker.is_logged_in():
                return
            status = self.engine.broker.keepalive()
            self.log(f"keepalive: status={status}")
            if status == "LOGIN":
                from .alerts import alert_login_required
                alert_login_required()
        except Exception as exc:
            self.log(f"keepalive failed: {exc}")

    def task_buy(self) -> None:
        res = self.engine.buy_cycle()
        self.log(f"buy: {res}")

    def task_sell(self) -> None:
        res = self.engine.sell_cycle()
        self.log(f"sell: {res}")

    def task_close(self) -> None:
        rep = self.engine.daily_report()
        out = self.cfg.path("report_dir") / f"close_{self.engine.today()}.json"
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        self.log(f"close report -> {out}")

    def _daily_last_date(self) -> str:
        """Newest Jalali session present in the daily files (sampled)."""
        try:
            import pandas as _pd
            d = self.cfg.path("daily_dir")
            if not d.exists():
                d = ROOT / "data" / "daily"
            last = ""
            for f in list(d.glob("*_daily.csv"))[:120]:
                try:
                    df = _pd.read_csv(f, usecols=["date"], dtype={"date": str})
                    if len(df):
                        last = max(last, str(df["date"].iloc[-1]))
                except Exception:
                    continue
            return last
        except Exception:
            return ""

    def _daily_symbols_count(self) -> int:
        try:
            d = self.cfg.path("daily_dir")
            if not d.exists():
                d = ROOT / "data" / "daily"
            return len(list(d.glob("*_daily.csv")))
        except Exception:
            return 0

    def task_clock(self) -> None:
        """Keep the bot clock aligned with real time (the OS clock is/was 30 min off)."""
        try:
            from .clock import calibrate
            r = calibrate()
            self.log(f"clock: stored={r['stored']:.0f}s measured={r['measured']} changed={r['changed']}")
        except Exception as exc:
            self.log(f"clock calibration failed: {exc}")

    def task_picks(self) -> None:
        sig = self.cfg.section("signals")
        if str(sig.get("source")) == "ride_locked":
            # nightly scan AFTER the data refresh: today's locked symbols, scored by
            # the durability model, written to state/candidates_next.csv
            from . import ride_locked
            try:
                df = ride_locked.write_candidates(self.cfg)
                self.log(f"ride candidates for next session: "
                         f"{[(r['symbol'], r['prob']) for _, r in df.iterrows()]}")
                # nightly email: download finished + the candidate list
                try:
                    from .alerts import alert_nightly_summary
                    from .calendar_ir import TradingCalendar
                    rows = []
                    for _, r in df.iterrows():
                        close = float(r.get("close") or 0)
                        ceil = float(r.get("ceiling_today") or 0)
                        rows.append({"symbol": r["symbol"], "prob": r.get("prob"),
                                     "dist_pct": ((ceil - close) / close * 100) if close else 0,
                                     "close": close,
                                     "next_ceiling": r.get("next_ceiling_est")})
                    cal = TradingCalendar()
                    last = str(df["for_session"].iloc[0]) if len(df) else ""
                    stats = {"symbols": self._daily_symbols_count(),
                             "last_date": self._daily_last_date()}
                    ok = alert_nightly_summary(rows, last, stats)
                    self.log(f"nightly email sent={ok}")
                except Exception as exc:
                    self.log(f"nightly email failed: {exc}")
            except Exception as exc:
                self.log(f"ride candidates failed: {exc}")
            return
        if str(sig.get("source")) == "queue_v3":
            from .signals_v3 import generate_picks
        else:
            from .signals import generate_picks
        picks = generate_picks(self.cfg, write=True)
        self.log(f"picks written: {[(p.symbol, round(p.score, 3)) for p in picks]}")

    def _run_script(self, name: str, args: list[str]) -> int:
        script = ROOT / "scripts" / name
        if not script.exists():
            self.log(f"skip {name}: not found")
            return -1
        cmd = [sys.executable, "-u", str(script), *args]
        try:
            pr = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                                timeout=3600, encoding="utf-8", errors="replace")
            tail = (pr.stdout or "").strip().splitlines()[-5:]
            self.log(f"{name} rc={pr.returncode} :: " + " | ".join(tail))
            return pr.returncode
        except Exception as exc:
            self.log(f"{name} failed: {exc}")
            return -1

    def task_data_refresh(self) -> None:
        self._run_script("update_data.py", [])

    def task_features(self) -> None:
        top = int(self.cfg.get("signals.feature_top_n", 120))
        self._run_script("build_day_features.py", ["--top", str(top)])

    # ------------------------------------------------------------------- loop
    def tick(self) -> None:
        now = now_tehran()
        day = now.date().isoformat()
        hm = _hm(now)
        sch = self.cfg.section("schedule")
        mkt = self.cfg.section("market")

        if self.cfg.path("state_dir").joinpath("STOP").exists():
            return

        # Morning tasks are windowed: without an upper bound they would re-run all
        # evening (and, in live mode, hammer a logged-out broker).
        pf = str(sch.get("preflight", "08:20"))
        if hm >= str(sch.get("clock_check", "08:00")) and self._once(day, "clock"):
            self._safe(self.task_clock)
        if pf <= hm <= "09:10" and self._once(day, "preflight"):
            if time.time() - self._preflight_try >= 300:
                self._preflight_try = time.time()
                self._safe(self.task_preflight)

        lw = str(sch.get("login_watch", "08:10"))
        if lw <= hm <= "09:05" and time.time() - self._login_watch_try >= 300:
            self._login_watch_try = time.time()
            self._safe(self.task_login_watch)

        if "08:00" <= hm <= "12:40" and time.time() - self._keepalive_try >= float(self.cfg.get("schedule.keepalive_sec", 600)):
            self._keepalive_try = time.time()
            self._safe(self.task_keepalive)

        po = str(sch.get("preopen_plan", "08:40"))
        if po <= hm <= "09:05" and self._once(day, "preopen"):
            self._safe(self.task_preopen_plan)

        # sells: pre-open auction, continuous, closing auction
        session = self.engine.cal.session(mkt, now)
        if session in ("pre_open", "continuous", "closing_auction"):
            interval = float(self.cfg.get("sell.check_interval_sec", 60))
            if time.time() - self._last_sell >= interval:
                self._last_sell = time.time()
                self._safe(self.task_sell)

        # buys: one attempt in the pre-open (08:45 by default), then a burst every
        # `buy.interval_sec` seconds between window_start and window_end (09:00-09:05).
        # The burst matters: the opening seconds change constantly and one shot at
        # 09:00 often misses the price.
        b = self.cfg.section("buy")
        if not bool(b.get("enabled", True)):
            pre = "99:99"
        else:
            pre = str(b.get("preopen_attempt", "08:45"))
        # The opening auction runs 08:30-09:00, so keep retrying inside it instead
        # of a single 08:45 shot: fills only happen for orders resting before 09:00.
        if hm >= str(sch.get("live_scan", "09:00")) and hm < "09:01" and self._once(day, "live_scan"):
            self._safe(lambda: self._run_script("live_scan_0900.py", []))
        if pre <= hm < "09:00":
            every_po = float(b.get("interval_sec", 30))
            last_po = getattr(self, "_last_buy_pre", 0.0)
            if time.time() - last_po >= every_po:
                self._last_buy_pre = time.time()
                self._safe(self.task_buy)
        w0 = str(b.get("window_start", "09:00")) if bool(b.get("enabled", True)) else "99:99"
        w1 = str(b.get("window_end", "09:05"))
        every = float(b.get("interval_sec", 30))
        if w0 <= hm <= w1:
            last = getattr(self, "_last_buy", 0.0)
            if time.time() - last >= every:
                self._last_buy = time.time()
                self._safe(self.task_buy)

        if hm >= str(sch.get("close_report", "13:10")) and self._once(day, "close"):
            self._safe(self.task_close)
        if hm >= str(sch.get("nightly_refresh", "20:30")) and self._once(day, "data"):
            self._safe(self.task_data_refresh)
        if hm >= str(sch.get("features", "21:00")) and self._once(day, "features"):
            self._safe(self.task_features)
        if hm >= str(sch.get("picks", "21:20")) and self._once(day, "picks"):
            self._safe(self.task_picks)
        # RealLern: refresh the 5-session candidate tracker right after the scan
        if hm >= str(sch.get("reallern", "21:25")) and self._once(day, "reallern"):
            self._safe(lambda: self._run_script("reallern_tracker.py", []))

    def _safe(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            self.log("TASK ERROR\n" + traceback.format_exc()[-1500:])

    @staticmethod
    def _plus_minutes(hm: str, minutes: int) -> str:
        h, m = (int(x) for x in hm.split(":"))
        total = h * 60 + m + minutes
        return f"{(total // 60) % 24:02d}:{total % 60:02d}"

    def run_forever(self, stop_after: Optional[int] = None) -> None:
        hb = float(self.cfg.get("schedule.heartbeat_sec", 20))
        n = 0
        self.log("scheduler started")
        while True:
            self.tick()
            n += 1
            if stop_after and n >= stop_after:
                self.log("scheduler stopping (stop_after reached)")
                return
            time.sleep(hb)













