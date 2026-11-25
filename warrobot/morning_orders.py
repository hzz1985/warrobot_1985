"""Single morning orchestrator for the pre-open (Hark fix, bug #1).

Replaces the crowd of jobs the scheduler fired at 08:44-08:45 (pwreset, lotsell,
preexit, order, ready45), which ran ONE AFTER ANOTHER in the scheduler loop and
fought over one browser profile:
  * pw_reset at 08:44 killed the logged-in Chrome right before the order;
  * the scheduler loop is synchronous: on 1405-09-01..04 the 08:40 'preready'
    job took ~9 minutes, so at 08:49 the loop fired preflight -> lotsell ->
    preexit -> preclean -> pwreset -> order in sequence and the first buy left
    ~12 minutes later (orders_log), not at 08:45:01;
  * order_candidates did all its network work (balance, open orders, holdings,
    TSETMC lookups) AFTER 08:45:01;
  * run.py's daemon fired its own order_candidates at 08:45 in parallel.

This script is started once (scheduler job "morning", 08:41) and owns the broker
browser until the pre-open orders are out:

  08:41      calibrate the clock against the broker/TSETMC HTTP Date header,
             make sure the ONE shared broker session is alive (login if needed),
             take the UI priority (state/ui_priority) so data agents step aside
  08:41-44   prepare: pre-open exits (exit_engine) + buys (order_candidates.prepare)
  ~08:44:20  start the first order flow: flow.js binds the symbol and fills the
             ticket, then waits until EXACTLY 08:45:01.000 Tehran and clicks
  after      the remaining orders back-to-back, never after 08:59:30
  state/morning_orders.json records target, actual click time and lag (ms).

    python morning_orders.py            live
    python morning_orders.py --dry      everything except the clicks
    python morning_orders.py --at 08:45:01   (another target, rehearsals)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE.parent / "TeseRobot" / "src"))

TEHRAN = ZoneInfo("Asia/Tehran")
STATE = BASE / "state"
OUT = STATE / "morning_orders.json"
PRIORITY = STATE / "ui_priority"
START_LEAD_SEC = 40          # flow.js needs ~10-30 s to bind + fill before the click
DEADLINE = dtime(8, 59, 30)  # pre-open ends 09:00; nothing is sent after this


def log(msg: str) -> None:
    line = f"[morning {datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with (STATE / "morning_orders.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# ----------------------------------------------------------------------- clock
def calibrate_clock() -> dict:
    """Measure OS-clock drift vs the broker/TSETMC Date header and store it.

    The stored system.clock_offset_sec (1800 s, set 2026-10-21 when the host was
    30 min slow) was never re-measured by the live stack. If the host clock has
    been corrected since, now_tehran() runs 30 min AHEAD and the "08:45:01" order
    leaves at 08:15 real time (rejected before the pre-open)."""
    try:
        from tsebot import clock
        return clock.calibrate(min_change_sec=5.0)
    except Exception as exc:
        return {"error": str(exc)[:120]}


def clock_offset() -> float:
    """Seconds now_tehran() adds to the OS clock."""
    try:
        from tsebot import clock
        return float(clock.current_offset())
    except Exception:
        return 0.0


def tehran_now(offset: float, now_fn=time.time) -> datetime:
    return datetime.fromtimestamp(now_fn() + offset, TEHRAN)


def os_epoch_for(target: datetime, offset: float) -> float:
    """OS-clock epoch (what JS Date.now()/time.time() read) of a real Tehran moment."""
    return target.timestamp() - offset


def sleep_until(os_epoch: float, now_fn=time.time, sleep_fn=time.sleep) -> None:
    """Coarse then fine sleep; returns at os_epoch (+ a few ms)."""
    while True:
        left = os_epoch - now_fn()
        if left <= 0:
            return
        sleep_fn(min(max(left - 0.05, 0.001), 5.0) if left > 0.06 else 0.001)


def parse_target(day: datetime, hhmmss: str) -> datetime:
    h, m, s = (int(x) for x in (hhmmss.split(":") + ["0", "0"])[:3])
    return datetime.combine(day.date(), dtime(h, m, s), tzinfo=TEHRAN)


def default_target_str() -> str:
    try:
        b = json.loads((BASE / "config.json").read_text(encoding="utf-8"))["buy"]
        return f"{b.get('preopen_attempt', '08:45')}:{int(b.get('preopen_second', 1)):02d}"
    except Exception:
        return "08:45:01"


def first_side() -> str:
    try:
        b = json.loads((BASE / "config.json").read_text(encoding="utf-8"))["buy"]
        return str(b.get("preopen_first", "sell"))
    except Exception:
        return "sell"


# ------------------------------------------------------------------- priority
def take_priority() -> None:
    PRIORITY.parent.mkdir(parents=True, exist_ok=True)
    PRIORITY.write_text(f"{os.getpid()}|{time.time()}|morning", encoding="utf-8")


def drop_priority() -> None:
    try:
        if PRIORITY.read_text(encoding="utf-8").split("|")[0] == str(os.getpid()):
            PRIORITY.unlink()
    except Exception:
        pass


# ------------------------------------------------------------------ the queue
def build_queue(sell_decisions: list, buy_plan: dict, order: str = "sell") -> list[dict]:
    sells = [{"side": "sell", "decision": d} for d in sell_decisions if d.action == "sell"]
    # qty 0 would let flow.js fall back to the whole per-ticket limit («آستانه حجم»),
    # regardless of the budget - never send that unattended
    buys = [{"side": "buy", "rec": r} for r in (buy_plan.get("planned") or [])
            if int(r.get("qty") or 0) > 0 and float(r.get("price") or 0) > 0]
    return sells + buys if order == "sell" else buys + sells


def run_queue(queue: list[dict], target_os_epoch: float, deadline_os_epoch: float,
              send_sell, send_buy, now_fn=time.time, sleep_fn=time.sleep) -> list[dict]:
    """Send every queued order: the first one clicks exactly at target_os_epoch."""
    results = []
    first = True
    for item in queue:
        if now_fn() > deadline_os_epoch:
            results.append({"side": item["side"], "skipped": "pre-open deadline passed"})
            continue
        at_ms = 0
        if first:
            # start the flow early enough to bind + fill, it then waits to the ms
            sleep_until(target_os_epoch - START_LEAD_SEC, now_fn, sleep_fn)
            at_ms = int(target_os_epoch * 1000)
        first = False
        t0 = now_fn()
        res = send_sell(item["decision"], at_ms) if item["side"] == "sell" else send_buy(item["rec"], at_ms)
        res = dict(res or {})
        res.setdefault("side", item["side"])
        res["flow_sec"] = round(now_fn() - t0, 2)
        if at_ms and res.get("click_epoch_ms"):
            res["click_lag_ms"] = int(res["click_epoch_ms"]) - at_ms
        results.append(res)
    return results


# ------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry" in argv
    target_str = argv[argv.index("--at") + 1] if "--at" in argv else default_target_str()
    report: dict = {"started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "dry": dry}

    try:
        import market_cal
        if not market_cal.market_day() and "--force" not in argv:
            report["skipped"] = "market_closed"
            OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
            return 0
    except Exception:
        pass

    report["clock"] = calibrate_clock()
    offset = clock_offset()
    now_t = tehran_now(offset)
    target = parse_target(now_t, target_str)
    deadline = datetime.combine(now_t.date(), DEADLINE, tzinfo=TEHRAN)
    target_os, deadline_os = os_epoch_for(target, offset), os_epoch_for(deadline, offset)
    report.update(offset_sec=offset, target_tehran=target.isoformat(),
                  now_tehran=now_t.isoformat(timespec="milliseconds"))
    if now_t > deadline:
        report["skipped"] = "started after the pre-open deadline"
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 1
    log(f"target {target_str} Tehran, clock offset {offset:+.0f}s, now {now_t:%H:%M:%S}")

    # every node script started from here (pnl, open orders, balance, flow.js...)
    # must pass the UI priority we hold, otherwise it would wait for ourselves
    os.environ["WARROBOT_UI_PRIORITY"] = "1"
    take_priority()
    try:
        import session_keeper
        sess = {}
        for attempt in range(3):
            sess = session_keeper.check(allow_login=True, priority=True)
            if sess.get("ok"):
                break
            log(f"session not ready ({sess.get('state')}), retry {attempt + 1}")
            if time.time() > target_os - 90:
                break
            time.sleep(20)
        report["session"] = {k: sess.get(k) for k in ("ok", "state", "logged_in_now", "api_status")}

        import pw_lock
        with pw_lock.guard("morning_orders", wait=240) as got:
            report["lock"] = bool(got)
            import exit_engine
            import order_candidates
            # yesterday's leftovers block the open-order guard (was job "preclean"
            # 08:43; inside the orchestrator it can never run AFTER today's orders)
            rehearsal = dry or os.environ.get("FLOW_DRY") == "1"
            if time.time() < target_os - 90 and not rehearsal:
                try:
                    import preopen_cleanup
                    preopen_cleanup.main()
                except Exception as exc:
                    log(f"preopen cleanup failed: {str(exc)[:120]}")
            try:
                cyc = exit_engine.prepare_cycle(rebuild_panel=True)
                sells = cyc["decisions"]
                positions = cyc["positions"]
            except Exception as exc:
                log(f"exit prepare failed: {str(exc)[:120]}")
                sells, positions = [], []
            try:
                buy_plan = order_candidates.prepare()
            except Exception as exc:
                log(f"buy prepare failed: {str(exc)[:120]}")
                buy_plan = {"planned": [], "note": f"prepare_error {str(exc)[:80]}"}
            report["exit_decisions"] = [{"symbol": d.symbol, "action": d.action, "reason": d.reason,
                                         "qty": d.qty, "price": d.order_price, "note": d.note}
                                        for d in sells]
            report["buy_plan"] = {"planned": buy_plan.get("planned"), "note": buy_plan.get("note"),
                                  "free_balance": buy_plan.get("free_balance")}
            queue = build_queue(sells, buy_plan, first_side())
            report["queue"] = [(q["side"], (q.get("rec") or {}).get("symbol") or q["decision"].symbol)
                               for q in queue]
            log(f"queue: {report['queue']}")

            def send_sell(decision, at_ms):
                recs = exit_engine.execute([decision], positions, "preopen", dry=dry,
                                           at_epoch_ms=at_ms, priority=True)
                return recs[0] if recs else {}

            def send_buy(rec, at_ms):
                res = order_candidates.execute({"planned": [rec]}, dry=dry, at_epoch_ms=at_ms,
                                               priority=True)
                return (res.get("orders") or [{}])[0]

            report["results"] = run_queue(queue, target_os, deadline_os, send_sell, send_buy)
    finally:
        drop_priority()

    report["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report["submitted"] = sum(1 for r in report.get("results", []) if r.get("submitted"))
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log(f"done: submitted={report['submitted']} of {len(report.get('results', []))}")
    print(json.dumps({k: report.get(k) for k in ("target_tehran", "offset_sec", "submitted", "queue")},
                     ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
