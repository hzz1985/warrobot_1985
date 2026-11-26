"""Standalone scheduler for the daily pipeline.

Runs its own loop (every 10-20 s) and uses PERSISTENT markers, so a missed tick or
a daemon restart can never skip a job again.

Hark fix #1 (why 08:45:01 never happened):
  * jobs used to run SYNCHRONOUSLY inside this loop: one slow job (08:40
    'preready' took ~9 min on 1405-09-01..04) delayed every later job, and at
    08:49 the loop fired preflight -> lotsell -> preexit -> preclean -> pwreset ->
    order one after another, so the buy left ~12 minutes late;
  * pwreset (08:44) killed the logged-in browser right before the order;
  * lotsell / preexit / order / ready45 all at 08:45 fought for one profile.
Now:
  * every job is started as a background process (never blocks the loop), a job
    is never started twice while it is still running, its output goes to
    state/joblogs/<job>.log;
  * ONE morning job (08:41, morning_orders.py) owns the browser through the
    pre-open: session check, cancel leftovers, pre-open exits + buys, and it
    clicks the first order at exactly 08:45:01.000 Tehran (flow.js waits to the ms);
  * jobs that need the morning result ("after") wait for it to finish;
    browser-heavy checks have an "until" time so they never run into the order;
  * the clock offset (system.clock_offset_sec) is re-measured at start-up and at
    07:50 (it was a static +1800 s);
  * a shared, logged-in broker Chrome is kept alive by session_keeper.py every
    minute (bug #2), instead of each job logging in again.

Jobs (Tehran time): see JOBS / REPEAT below.
Completion is written to state/jobs_done.json as {"YYYY-MM-DD": {"job": "HH:MM"}};
"<job>#done" records when a job finished.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
if not BASE.exists():                       # tests / another checkout
    BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

try:
    import distbot as bot  # noqa: E402
except Exception:          # pragma: no cover - only when tsebot is missing (tests)
    bot = None
import market_cal  # noqa: E402

STATE = BASE / "state" / "jobs_done.json"
JOBLOGS = BASE / "state" / "joblogs"

# (at, name, argv[, opts])   opts: {"until": "HH:MM"} never start at/after it,
#                                  {"after": "job"} start only once that job finished today
BROWSER_CHECK = {"until": "08:41"}
AFTER_MORNING = {"after": "morning"}
JOBS = [
    ("07:50", "clock", ["clock_sync.py"]),             # re-measure the OS clock drift
    ("08:20", "verify", ["verify_before_session.py"], BROWSER_CHECK),
    ("08:22", "prices", ["nightly_prices.py"]),      # fresh balance before pre-open
    ("08:25", "selfcheck", ["selfcheck_rules.py"]),
    ("08:27", "rehearse", ["rehearse_morning.py"], BROWSER_CHECK),   # dress rehearsal, no orders
    ("08:30", "ready30", ["ready_check.py"], BROWSER_CHECK),
    ("08:33", "preready", ["preopen_ready.py"], BROWSER_CHECK),       # prices + P&L
    ("08:36", "preflight", ["preflight.py"], BROWSER_CHECK),
    ("08:38", "ready40", ["ready_check.py"], BROWSER_CHECK),          # the "08:40" readiness mail
    # removed: 08:38 loginsync (overwrote the bot cookie DB + forced a login),
    #          08:44 pwreset (killed the logged-in browser right before the order),
    #          08:43 preclean / 08:45 lotsell / preexit / order (now inside "morning")
    ("08:41", "morning", ["morning_orders.py"]),     # owns the browser, order at 08:45:01.000
    ("08:45", "ready45", ["ready_check.py"], AFTER_MORNING),
    # Hark F3 (2026-11-28): fundamentals strategy. 08:47 sells the افران excess then buys the F3 names as
    # buying power arrives; the later runs buy whatever is still missing (idempotent, Tehran-day guards).
    ("08:47", "fundbuy", ["fund_orders.py"], AFTER_MORNING),
    ("09:05", "fundbuy2", ["fund_orders.py"]),
    ("09:40", "fundbuy3", ["fund_orders.py"]),
    ("10:20", "fundbuy4", ["fund_orders.py"]),
    ("11:20", "fundbuy5", ["fund_orders.py"]),
    ("12:10", "fundbuy6", ["fund_orders.py"]),
    ("08:46", "ordsup", ["order_supervisor.py", "--budget", "240", "--max-attempts", "3"], AFTER_MORNING),
    ("08:52", "orderretry", ["order_retry.py"], AFTER_MORNING),
    ("08:56", "ordsup2", ["order_supervisor.py", "--budget", "180", "--max-attempts", "2"], AFTER_MORNING),
    ("09:02", "truth", ["order_truth.py"]),          # real order result, not a guess
    ("12:35", "truthend", ["order_truth.py"]),     # retry if nothing was submitted
    ("10:00", "rotate", ["rebalance.py"]),
    ("21:12", "salesrec", ["agents/sale_reconcile.py"]),
    ("21:14", "trades", ["trades_ledger.py"]),
    ("21:15", "scan", ["queue_scan.py"]),
    ("21:16", "report", ["report_periods.py"]),
    ("21:17", "journal", ["trade_journal.py", "--sync"]),
    ("21:40", "agents_night", ["agents/supervisor.py"]),
    ("21:55", "readiness_night", ["readiness_check.py"]),
    ("21:47", "weekly", ["agents/weekly_report.py"]),
    ("21:50", "calsync", ["agents/calendar_sync.py"]),
    ("21:52", "calendar", ["agents/calendar_watch.py"]),
    ("21:18", "prices_night", ["nightly_prices.py"]),
    ("21:20", "portsync", ["portfolio_sync.py"]),
    ("21:21", "ledgersync", ["ledger_sync.py"]),
    ("21:25", "sellrev", ["sell_monitor.py"]),
    ("21:35", "webrefresh", ["web_refresh.py"]),
    ("21:30", "fundplan", ["fund_live.py", "--plan"]),   # every day: Saturday's plan is built Friday night
    ("22:00", "selfcheckpm", ["selfcheck_rules.py"]),
    ("20:30", "download", ["nightly_download.py"]),
    ("23:50", "pm2save", ["pm2_save.py"]),
]

CLOSED_DAY_DATA = {"selfcheck", "selfcheckpm", "pm2save", "download", "clock",
                   "prices", "prices_night", "report", "journal", "trades", "webrefresh",
                   "portsync", "ledgersync", "sellrev", "weekly", "calendar", "calsync",
                   "salesrec", "saleprice", "soldpnl", "fundplan"}

# jobs that run over and over inside a time window: (from, to, every_min, name, script)
REPEAT = [
    # the ONE shared broker session: keep-alive + re-login only when really logged out
    ("07:50", "12:45", 1, "session", "session_keeper.py --once"),
    ("12:46", "23:59", 30, "session_off", "session_keeper.py --once --no-login"),
    # per-card data agents (their own timers)
    ("09:00", "12:30", 5, "d_balance", "agents/data_agents.py --job balance"),
    ("12:35", "23:59", 30, "d_balance_off", "agents/data_agents.py --job balance"),
    ("00:00", "08:29", 30, "d_balance_night", "agents/data_agents.py --job balance"),
    ("09:00", "12:30", 10, "d_portfolio", "agents/data_agents.py --job portfolio"),
    ("12:35", "23:59", 60, "d_portfolio_off", "agents/data_agents.py --job portfolio"),
    ("09:00", "12:30", 10, "d_price", "agents/order_price.py"),
    ("09:00", "12:30", 30, "slots", "agents/slots.py"),
    ("09:00", "12:30", 10, "portpnl", "agents/portfolio_pnl.py"),
    ("09:00", "12:30", 30, "tshealth", "agents/tsetmc_health.py"),
    ("12:35", "23:59", 60, "portpnl_off", "agents/portfolio_pnl.py"),
    ("09:00", "12:30", 10, "d_sell", "agents/data_agents.py --job sell"),
    ("12:35", "23:59", 60, "d_sell_off", "agents/data_agents.py --job sell"),
    ("09:00", "12:30", 15, "d_pnl", "agents/data_agents.py --job pnl"),
    ("12:35", "23:59", 60, "d_pnl_off", "agents/data_agents.py --job pnl"),
    ("00:00", "23:59", 10, "guard", "agents/sleep_guard.py"),
    ("08:00", "21:00", 60, "news", "agents/news_watch.py"),
    ("08:05", "12:30", 15, "agents", "agents/supervisor.py"),
    ("09:00", "17:00", 15, "tgcollect", "research/tg_channel_collector.py"),
    # TP/SL exits: exit_engine via intraday_exit (bug #3); 09:03-12:30 every 3 min
    ("09:00", "12:30", 3, "exitwatch", "intraday_exit.py"),
    ("08:46", "09:45", 5, "ordermon", "order_monitor.py"),
    ("09:00", "12:30", 3, "pnlrefresh", "pnl.py"),
]
REPEAT_OFF_DAY = {"guard", "d_balance", "d_portfolio", "d_pnl", "d_sell", "slots", "portpnl",
                  "tshealth", "session_off"}
QUIET_FROM, QUIET_TO = "08:30", "09:03"      # the pre-open owns the browser
QUIET_EXEMPT = {"session", "guard"}

RUNNING: dict[str, dict] = {}


def log(msg: str) -> None:
    if bot is not None:
        bot.log(msg)
    else:
        print(msg, flush=True)


def now_tehran():
    return bot.now_tehran() if bot is not None else market_cal.now_tehran()


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def _minutes(hhmm: str) -> int:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def wait_for_084501() -> None:
    """Kept for compatibility (selfcheck_rules looks for it).

    The precise wait now happens inside the order flow itself: morning_orders.py
    passes FLOW_AT_EPOCH_MS to pw/flow.js, which binds the symbol and fills the
    ticket first and clicks at 08:45:01.000 Tehran (clock-offset corrected)."""
    import time as _t
    from datetime import timedelta as _td
    now = now_tehran()
    target = now.replace(hour=8, minute=45, second=1, microsecond=0)
    if now >= target or (target - now) > _td(minutes=30):
        return
    while True:
        now = now_tehran()
        if now >= target:
            return
        _t.sleep(min(0.2, max(0.02, (target - now).total_seconds())))


def sweep_orphans() -> None:
    """Remove a Chrome that holds the profile WITHOUT answering CDP.

    The shared, logged-in broker Chrome is never killed here any more."""
    try:
        import pw_reset
        n = pw_reset.kill_orphans()
        if n:
            log(f"[sched] swept {n} orphan chrome")
    except Exception:
        pass


def calibrate_clock() -> None:
    try:
        sys.path.insert(0, str(BASE.parent / "TeseRobot" / "src"))
        from tsebot import clock
        r = clock.calibrate(min_change_sec=5.0)
        log(f"[sched] clock calibrate {r}")
    except Exception as exc:
        log(f"[sched] clock calibrate error {str(exc)[:100]}")


def _start(job: str, script: str, extra_args: list | None = None) -> bool:
    """Start a job in the background; never blocks the loop."""
    if job in RUNNING:
        return False
    try:
        JOBLOGS.mkdir(parents=True, exist_ok=True)
        fh = (JOBLOGS / f"{job}.log").open("a", encoding="utf-8", errors="replace")
        fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start {script} {' '.join(extra_args or [])}\n")
        fh.flush()
        flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
        p = subprocess.Popen([sys.executable, "-u", str(BASE / script)] + list(extra_args or []),
                             cwd=str(BASE), stdout=fh, stderr=subprocess.STDOUT,
                             creationflags=flags)
        RUNNING[job] = {"p": p, "fh": fh, "t0": time.time(), "script": script}
        return True
    except Exception as exc:
        log(f"[sched] {job} start error: {str(exc)[:120]}")
        return False


def _reap(data: dict, day: str) -> None:
    """Collect finished jobs: log rc + tail, mark '<job>#done', kill hung ones."""
    for job, info in list(RUNNING.items()):
        p = info["p"]
        rc = p.poll()
        if rc is None:
            if time.time() - info["t0"] > 3600:
                p.kill()
                log(f"[sched] {job} killed after 3600 s")
            continue
        try:
            info["fh"].close()
            tail = (JOBLOGS / f"{job}.log").read_text(encoding="utf-8", errors="replace")[-160:]
        except Exception:
            tail = ""
        log(f"[sched] {job} rc={rc} in {time.time() - info['t0']:.0f}s :: {tail}")
        data.setdefault(day, {})[f"{job}#done"] = now_tehran().strftime("%H:%M:%S")
        RUNNING.pop(job, None)
        sweep_orphans()


def due_jobs(today: dict, hm: str, trading: bool) -> list[tuple]:
    """Pure: which JOBS fire now -> [(job, argv, state)] with state fire|closed|skipped|wait."""
    out = []
    for entry in JOBS:
        at, job, argv = entry[0], entry[1], entry[2]
        opts = entry[3] if len(entry) > 3 else {}
        if job in today or hm < at:
            continue
        if not trading and job not in CLOSED_DAY_DATA:
            out.append((job, argv, "closed"))
            continue
        # a job only fires inside its own window (20 minutes); anything later
        # is recorded as skipped so a restart never replays the morning jobs
        late = _minutes(hm) - _minutes(at)
        if late > 20 or (opts.get("until") and hm >= opts["until"]):
            out.append((job, argv, "skipped"))
            continue
        after = opts.get("after")
        if after and f"{after}#done" not in today and \
                not str(today.get(after, "")).startswith(("closed", "skipped")):
            out.append((job, argv, "wait"))
            continue
        out.append((job, argv, "fire"))
    return out


def due_repeats(today: dict, hm: str, trading: bool) -> list[tuple]:
    out = []
    for at_from, at_to, every, job, script in REPEAT:
        if not trading and job not in REPEAT_OFF_DAY:
            continue
        if QUIET_FROM <= hm <= QUIET_TO and job not in QUIET_EXEMPT:
            continue
        if not (at_from <= hm <= at_to):
            continue
        key = f"{job}-{_minutes(hm) // max(1, every)}"
        if key in today:
            continue
        out.append((job, script, key))
    return out


def main() -> int:
    log("[sched] scheduler started (background jobs, single morning orchestrator)")
    calibrate_clock()
    while True:
        now = now_tehran()
        day, hm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        data = _load()
        today = data.setdefault(day, {})
        if RUNNING:
            n_before = len(RUNNING)
            _reap(data, day)
            if len(RUNNING) != n_before:
                _save(data)
        trading = market_cal.market_day(now)
        fired = False
        for job, argv, state in due_jobs(today, hm, trading):
            if state == "wait":
                continue
            if state == "closed":
                today[job] = f"closed@{hm}"
            elif state == "skipped":
                today[job] = f"skipped@{hm}"
                log(f"[sched] {job} skipped at {hm}")
            elif _start(job, argv[0], argv[1:]):
                today[job] = hm
                fired = True
                log(f"[sched] started {job} at {hm}")
            _save(data)
        for job, script, key in due_repeats(today, hm, trading):
            if job in RUNNING:
                continue                      # still busy: never stack the same job
            parts = str(script).split()
            if _start(job, parts[0], parts[1:]):
                today[key] = hm
                _save(data)

        # prune old days
        if len(data) > 14:
            for key in sorted(data)[:-10]:
                data.pop(key, None)
            _save(data)
        time.sleep(5 if fired or "08:38" <= hm <= "08:47" else 15)


if __name__ == "__main__":
    raise SystemExit(main())
