# -*- coding: utf-8 -*-
"""TSE Bot v2 — command line entry point.

Commands
--------
init        write a config template and a sample paper snapshot
picks       build tomorrow's pick list from the latest daily bars
sync        import broker fills into the ledger and reconcile
report      print the accounting snapshot (ledger truth)
selftest    full dry-run cycle on the paper broker (no market risk)
backtest    run the honest backtest and save the report
probe       read-only broker diagnostics (no orders, live mode only)
daemon      run the scheduler; requires --i-understand-risk for live

Live trading is opt-in twice: mode must be "live" in the config AND the daemon
must be started with --i-understand-risk. Everything else is dry-run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config as config_mod
from .broker_base import BrokerError
from .calendar_ir import TradingCalendar, now_tehran
from .engine import Engine
from .ledger import Ledger
from .orders import OrderStore
from .scheduler import Scheduler
from .state import JsonState


def _log(msg: str) -> None:
    ts = now_tehran().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        p = config_mod.ROOT / "logs" / f"bot_{now_tehran().strftime('%Y%m%d')}.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"[{now_tehran().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _make_broker(cfg, paper_snapshot: str | None = None):
    kind = str(cfg.get("broker.kind", "selenium")).lower()
    # Dry-run normally uses the paper broker so it can never touch the market.
    # Setting broker.read_real_in_dry_run lets it *read* the real account (quotes,
    # order list) while still refusing to submit: the execution path checks
    # `cfg.is_live` before calling broker.submit().
    read_real = bool(cfg.get("broker.read_real_in_dry_run", False))
    if cfg.mode != "live" and kind != "paper" and not read_real:
        kind = "paper"
    if kind == "paper":
        from .broker_paper import PaperBroker
        snap = Path(paper_snapshot) if paper_snapshot else cfg.path("state_dir") / "paper_snapshot.json"
        return PaperBroker(snap, float(cfg.get("fees.buy_pct", 0.6)), float(cfg.get("fees.sell_pct", 0.6)))
    from .broker_selenium import SeleniumBroker
    return SeleniumBroker(
        debugger_address=str(cfg.get("broker.debugger_address")),
        chromedriver=str(cfg.get("broker.chromedriver")),
        trade_url=str(cfg.get("broker.trade_url")),
        page_settle_sec=float(cfg.get("broker.page_settle_sec", 3.0)),
    )


def _engine(cfg, paper_snapshot=None) -> Engine:
    broker = _make_broker(cfg, paper_snapshot)
    return Engine(cfg, broker, logger=_log)


def cmd_init(args) -> int:
    p = config_mod.save_template()
    paper = config_mod.ROOT / "state" / "paper_snapshot.json"
    if not paper.exists():
        paper.parent.mkdir(parents=True, exist_ok=True)
        paper.write_text(json.dumps({
            "buying_power": 500_000_000,
            "positions": {},
            "symbols": {
                "فولاد": {"last": 4400, "ceiling": 4620, "floor": 4180,
                          "best_bid": 4390, "best_ask": 4400, "max_qty": 100000},
                "شپاس": {"last": 58340, "ceiling": 61000, "floor": 55400,
                         "best_bid": 58300, "best_ask": 58340, "max_qty": 100000},
            },
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"config: {p}")
    print(f"paper : {paper}")
    return 0


def cmd_picks(args) -> int:
    cfg = config_mod.load(args.config)
    if str(cfg.get("signals.source")) == "queue_v3":
        from .signals_v3 import generate_picks
        picks = generate_picks(cfg, day=args.asof, write=True)
    else:
        from .signals import generate_picks
        picks = generate_picks(cfg, asof=args.asof, write=True)
    print(json.dumps([{"symbol": p.symbol, "score": round(p.score, 4)} for p in picks],
                     ensure_ascii=True, indent=2))
    print(f"written: {cfg.path('picks_dir') / 'picks_v2.json'}")
    return 0


def cmd_sync(args) -> int:
    cfg = config_mod.load(args.config)
    eng = _engine(cfg, args.paper)
    eng.connect()
    res = eng.sync()
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if not res.get("error") else 2


def cmd_report(args) -> int:
    cfg = config_mod.load(args.config)
    eng = _engine(cfg, args.paper)
    try:
        eng.connect()
    except Exception as exc:
        _log(f"connect failed ({exc}); reporting from ledger only")
    print(json.dumps(eng.account_snapshot(), ensure_ascii=False, indent=2))
    return 0


def cmd_selftest(args) -> int:
    cfg = config_mod.load(args.config)
    if getattr(args, "simulate", False):
        # Full path (submit -> order list -> ledger) on the paper broker:
        # live code, zero market risk.
        cfg.raw["mode"] = "live"
        cfg.raw.setdefault("broker", {})["kind"] = "paper"
        # A dedicated, wiped sandbox: simulations must be reproducible and must
        # never contaminate the real ledger.
        cfg.raw.setdefault("paths", {})["state_dir"] = "state/sim"
        sim_dir = cfg.path("state_dir")
        sim_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("ledger.jsonl", "orders.jsonl", "engine_state.json", "ran.json"):
            fp = sim_dir / fname
            if fp.exists():
                fp.unlink()
        if not args.paper:
            args.paper = str(config_mod.ROOT / "state" / "paper_snapshot.json")
    if getattr(args, "simulate", False):
        from datetime import datetime as _dt, time as _time
        from .calendar_ir import TEHRAN as _TZ
        hh, mm = (int(x) for x in str(getattr(args, "at", "10:00")).split(":"))
        fixed = _dt.combine(now_tehran().date(), _time(hh, mm), _TZ)
        eng = Engine(cfg, _make_broker(cfg, args.paper), now_fn=lambda: fixed, logger=_log)
    else:
        eng = _engine(cfg, args.paper)
    out = {"steps": []}
    eng.connect()

    out["steps"].append({"sync": eng.sync()})
    if getattr(args, "simulate", False):
        # Seed the ledger from the paper snapshot so the sell path has a position
        # to manage. This also proves ledger idsempotency on repeated runs.
        try:
            snap = json.loads(Path(args.paper).read_text(encoding="utf-8"))
            seeded = []
            for sym, qty in (snap.get("positions") or {}).items():
                if not qty:
                    continue
                pos = eng.ledger.get(sym)
                if pos and pos.qty:
                    continue
                cost = float((snap.get("cost_basis") or {}).get(sym) or 0) or 1.0
                eng.ledger.record_fill(sym, "buy", int(qty), cost, fee=0.0,
                                       source="seed", ts=fixed.isoformat(timespec="seconds"))
                seeded.append(sym)
            if seeded:
                seed_cash = float(snap.get("buying_power") or 0) + sum(
                    float((snap.get("cost_basis") or {}).get(s) or 0) * int((snap.get("positions") or {}).get(s) or 0)
                    for s in (snap.get("positions") or {}))
                eng.ledger.record_cash(seed_cash, "seed_open_positions")
            out["steps"].append({"seeded": seeded})
        except Exception as exc:
            out["steps"].append({"seed_error": str(exc)})
    out["steps"].append({"marks": eng.refresh_marks()})
    out["steps"].append({"sell": eng.sell_cycle()})
    out["steps"].append({"buy": eng.buy_cycle()})
    out["steps"].append({"account_after": eng.account_snapshot()})
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_backtest(args) -> int:
    cfg = config_mod.load(args.config)
    if args.start:
        cfg.raw.setdefault("backtest", {})["start"] = args.start
    if args.end:
        cfg.raw.setdefault("backtest", {})["end"] = args.end
    if args.optimistic:
        cfg.raw["backtest"]["fill_model"] = "optimistic"
    bt = cfg.raw.setdefault("backtest", {})
    if args.target is not None:
        bt["target_pct"] = args.target
    if args.stop is not None:
        bt["stop_pct"] = args.stop
    if args.max_hold is not None:
        bt["max_hold_days"] = args.max_hold
    if args.exit_mode:
        bt["exit_mode"] = args.exit_mode
    from .backtest import run_backtest, format_report
    res = run_backtest(cfg, fill_model=args.fill or None,
                       top_n=args.top_n, min_score=args.min_score,
                       select=args.select or "rank")
    text = format_report(res)
    print(text)
    outdir = cfg.path("report_dir")
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = now_tehran().strftime("%Y%m%d_%H%M%S")
    tag = f"{res.fill_model}_{cfg.get('backtest.exit_mode', 'barrier')}"
    (outdir / f"backtest_{tag}_{stamp}.txt").write_text(text, encoding="utf-8")
    (outdir / f"backtest_{tag}_{stamp}.json").write_text(
        json.dumps(res.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0


def cmd_adopt(args) -> int:
    cfg = config_mod.load(args.config)
    eng = _engine(cfg, args.paper)
    engine_state = JsonState(cfg.path("state_dir") / "engine_state.json")
    cur = list(engine_state.get("managed") or [])
    syms = [s.strip() for s in (args.symbols or "").split(",") if s.strip()]
    for s in syms:
        if s not in cur:
            cur.append(s)
    engine_state.set("managed", cur)
    print(json.dumps({"managed": cur}, ensure_ascii=True, indent=2))
    return 0


def cmd_probe(args) -> int:
    cfg = config_mod.load(args.config)
    if not cfg.is_live:
        print("probe requires mode=live (read-only, but it touches the real session)")
        return 2
    eng = _engine(cfg)
    eng.connect()
    info = {
        "logged_in": True,
        "session": eng.session(),
        "account": eng.broker.account().__dict__,
        "orders": [o.__dict__ for o in eng.broker.orderlist()][:50],
    }
    print(json.dumps(info, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_daemon(args) -> int:
    cfg = config_mod.load(args.config)
    if cfg.is_live and not args.i_understand_risk:
        print("REFUSING: mode=live requires --i-understand-risk")
        return 3
    eng = _engine(cfg)
    sched = Scheduler(cfg, eng, log=_log)
    if args.once:
        sched.tick()
        return 0
    sched.run_forever(stop_after=args.ticks)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tsebot", description="TSE Bot v2")
    p.add_argument("--config", default=None, help="path to config.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="write config template + paper snapshot")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("picks", help="build tomorrow's picks")
    sp.add_argument("--asof", default=None, help="Jalali day YYYY-MM-DD (default: latest bar)")
    sp.set_defaults(func=cmd_picks)

    sp = sub.add_parser("sync", help="import broker fills + reconcile")
    sp.add_argument("--paper", default=None)
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("report", help="print accounting snapshot")
    sp.add_argument("--paper", default=None)
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("selftest", help="full dry-run cycle on paper broker")
    sp.add_argument("--paper", default=None)
    sp.add_argument("--simulate", action="store_true",
                    help="execute the real order path against the paper broker")
    sp.add_argument("--at", default="10:00", help="simulated Tehran time HH:MM")
    sp.set_defaults(func=cmd_selftest)

    sp = sub.add_parser("backtest", help="honest backtest")
    sp.add_argument("--start", default=None)
    sp.add_argument("--end", default=None)
    sp.add_argument("--fill", default=None, choices=["open_if_not_locked", "optimistic"])
    sp.add_argument("--optimistic", action="store_true")
    sp.add_argument("--top-n", type=int, default=None)
    sp.add_argument("--min-score", type=float, default=None)
    sp.add_argument("--select", default="rank", choices=["rank", "random"],
                    help="rank = strategy, random = control group")
    sp.add_argument("--target", type=float, default=None)
    sp.add_argument("--stop", type=float, default=None)
    sp.add_argument("--max-hold", type=int, default=None)
    sp.add_argument("--exit-mode", default=None, choices=["barrier", "queue", "trail"])
    sp.set_defaults(func=cmd_backtest)

    sp = sub.add_parser("adopt", help="start managing specific existing positions")
    sp.add_argument("--symbols", required=True, help="comma separated")
    sp.add_argument("--paper", default=None)
    sp.set_defaults(func=cmd_adopt)

    sp = sub.add_parser("probe", help="read-only broker diagnostics")
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("daemon", help="run the scheduler")
    sp.add_argument("--once", action="store_true")
    sp.add_argument("--ticks", type=int, default=None)
    sp.add_argument("--i-understand-risk", action="store_true")
    sp.set_defaults(func=cmd_daemon)
    return p


def main(argv=None) -> int:
    # A redirected stdout on Windows defaults to cp1252; any Persian symbol name
    # in a log line would then raise mid-run. Force utf-8 once, here.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except BrokerError as exc:
        _log(f"broker error: {exc}")
        return 2
    except Exception as exc:
        import traceback
        _log("FATAL " + str(exc))
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())












