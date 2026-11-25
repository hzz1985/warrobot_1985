"""Standalone scheduler for the daily pipeline.

Runs its own loop (every 20 s) and uses PERSISTENT markers, so a missed tick or a
daemon restart can never skip a job again — the root cause of the "08:45 never
fired" incident.

Jobs (Tehran time):
    08:40  preflight.py         login + balance + candidates + ISIN
    08:45  order_candidates.py  single-shot ordering (1/3 of total assets each)
    10:00  rebalance.py         cancel unfilled + order the next candidate
    20:30  nightly download     (starts run.py's own pipeline; skipped if done)
    21:15  queue_scan.py        nightly scan -> 6 candidates
    21:25  sell_monitor.py      positions review + automatic exits
    21:35  web_refresh.py       refresh every web-app table

Each job runs at most once per day; completion is written to
state/jobs_done.json as {"YYYY-MM-DD": {"job": "HH:MM"}}.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

import distbot as bot  # noqa: E402
import market_cal  # noqa: E402

STATE = BASE / "state" / "jobs_done.json"
JOBS = [
    ("08:20", "verify", ["verify_before_session.py"]),
    ("08:25", "selfcheck", ["selfcheck_rules.py"]),
    ("08:30", "ready30", ["ready_check.py"]),
    ("08:40", "ready40", ["ready_check.py"]),
    ("08:27", "rehearse", ["rehearse_morning.py"]),   # dress rehearsal, no orders
    ("08:38", "loginsync", ["sync_login.py"]),        # reuse the operator's Chrome session
    ("08:40", "preready", ["preopen_ready.py"]),       # login retries + prices + P&L
    ("08:42", "preflight", ["preflight.py"]),
    # the order must fire at 08:45:01 before the 08:45 readiness email
    # unsold take-profit / stop-loss positions get their order first
    ("08:45", "lotsell", ["lot_exits.py", "--live"]),   # lot-based TP/SL at pre-open
    ("08:45", "preexit", ["preopen_exits.py"]),
    ("08:22", "prices", ["nightly_prices.py"]),      # fresh balance before pre-open
    ("08:43", "preclean", ["preopen_cleanup.py"]),    # cancel yesterday's leftovers
    ("08:44", "pwreset", ["pw_reset.py"]),           # free the browser profile
    ("08:45", "order", ["order_candidates.py"]),
    ("08:46", "ordsup", ["order_supervisor.py", "--budget", "240", "--max-attempts", "3"]),
    ("08:52", "orderretry", ["order_retry.py"]),
    ("08:56", "ordsup2", ["order_supervisor.py", "--budget", "180", "--max-attempts", "2"]),
    ("09:02", "truth", ["order_truth.py"]),          # real order result, not a guess
    ("12:35", "truthend", ["order_truth.py"]),     # retry if nothing was submitted
    ("08:45", "ready45", ["ready_check.py"]),
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
    ("21:18", "prices", ["nightly_prices.py"]),
    ("21:20", "portsync", ["portfolio_sync.py"]),
    ("21:21", "ledgersync", ["ledger_sync.py"]),
    ("21:25", "sellrev", ["sell_monitor.py"]),
    ("21:35", "webrefresh", ["web_refresh.py"]),
    ("22:00", "selfcheckpm", ["selfcheck_rules.py"]),
    ("20:30", "download", ["nightly_download.py"]),
    ("23:50", "pm2save", ["pm2_save.py"]),
]


# jobs that run over and over inside a time window: (from, to, every_min, name, script)
REPEAT = [
    # per-card data agents (their own timers)
    ("09:00", "12:30", 5, "d_balance", "agents/data_agents.py --job balance"),
    ("12:35", "23:59", 30, "d_balance_off", "agents/data_agents.py --job balance"),
    ("00:00", "08:59", 30, "d_balance_night", "agents/data_agents.py --job balance"),
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
    ("09:00", "12:30", 3, "exitwatch", "intraday_exit.py"),
    ("08:46", "09:45", 5, "ordermon", "order_monitor.py"),
    ("09:00", "12:30", 3, "pnlrefresh", "pnl.py"),
]


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _minutes(hhmm: str) -> int:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def wait_for_084501() -> None:
    """Block until 08:45:01 Tehran — NEVER order before the pre-open second.

    If the job starts early (restart, clock catch-up) it now waits for 08:45:01
    instead of returning immediately, which previously sent orders at 08:34 and
    they were rejected (no_order_response).
    """
    import time as _t
    from datetime import datetime as _dt, timedelta as _td
    now = bot.now_tehran()
    target = now.replace(hour=8, minute=45, second=1, microsecond=0)
    if now >= target:
        return                          # already past 08:45:01
    if (target - now) > _td(minutes=30):
        return                          # not a pre-open job run
    while True:
        now = bot.now_tehran()
        if now >= target:
            return
        _t.sleep(min(0.2, max(0.02, (target - now).total_seconds())))


def sweep_orphans() -> None:
    """Kill any Chrome left behind by the job that just finished."""
    try:
        import pw_reset
        n = pw_reset.kill_orphans()
        if n:
            bot.log(f"[sched] swept {n} orphan chrome")
    except Exception:
        pass


def _run(job: str, script: str, extra_args: list | None = None) -> None:
    try:
        extra = list(extra_args or [])
        p = subprocess.run([sys.executable, "-u", str(BASE / script)] + extra, cwd=str(BASE),
                           capture_output=True, text=True, timeout=3600,
                           encoding="utf-8", errors="replace")
        bot.log(f"[sched] {job} rc={p.returncode} :: {((p.stdout or '') + (p.stderr or ''))[-160:]}")
    except Exception as exc:
        bot.log(f"[sched] {job} error: {str(exc)[:120]}")


    sweep_orphans()

def main() -> int:
    bot.log("[sched] scheduler started")
    while True:
        now = bot.now_tehran()
        day, hm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        data = _load()
        today = data.setdefault(day, {})
        trading = market_cal.market_day(now)
        fired = False
        for at, job, argv in JOBS:
            if job in today:
                continue
            if hm < at:
                continue
            CLOSED_DAY_DATA = {"selfcheck", "selfcheckpm", "pm2save", "download",
                               "prices", "report", "journal", "trades", "webrefresh",
                               "portsync", "ledgersync", "sellrev", "weekly", "calendar", "calsync", "salesrec", "saleprice", "soldpnl"}
            if not trading and job not in CLOSED_DAY_DATA:
                if job not in today:
                    today[job] = f"closed@{hm}"
                    _save(data)
                continue
            # a job only fires inside its own window (20 minutes); anything later
            # is recorded as skipped so a restart never replays the morning jobs
            mins_late = _minutes(hm) - _minutes(at)
            if mins_late > 20:
                today[job] = f"skipped@{hm}"
                _save(data)
                bot.log(f"[sched] {job} skipped (missed by {mins_late} min)")
                continue
            bot.log(f"[sched] firing {job} at {at} (now {hm})")
            if job in ("order", "preexit"):
                wait_for_084501()
            _run(job, argv[0], argv[1:])
            today[job] = now.strftime("%H:%M")
            _save(data)
            fired = True
        # repeating jobs (e.g. the take-profit / stop-loss watch)
        for at_from, at_to, every, job, script in REPEAT:
            if not trading and job not in ("guard", "d_balance",
                                           "d_portfolio", "d_pnl", "d_sell", "slots", "portpnl", "tshealth"):
                break
            # quiet window: the pre-open order needs the browser lock alone
            if "08:30" <= hm <= "09:03":
                continue
            if not (at_from <= hm <= at_to):
                continue
            bucket = _minutes(hm) // max(1, every)
            key = f"{job}-{bucket}"
            if key in today:
                continue
            today[key] = hm
            _save(data)
            _parts = str(script).split()
            _run(job, _parts[0], _parts[1:])

        # prune old days
        if len(data) > 14:
            for key in sorted(data)[:-10]:
                data.pop(key, None)
            _save(data)
        time.sleep(10 if fired else 20)


if __name__ == "__main__":
    raise SystemExit(main())
