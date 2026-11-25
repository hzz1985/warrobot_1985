"""Verify that every change made on 2026-10-21 is actually live in the bot."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tsebot import config as config_mod  # noqa: E402
from tsebot.broker_selenium import SeleniumBroker  # noqa: E402
from tsebot.calendar_ir import now_tehran  # noqa: E402
from tsebot.engine import Engine  # noqa: E402
from tsebot.ledger import Ledger  # noqa: E402

ROWS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    ROWS.append((name, "PASS" if ok else "FAIL", detail))


def main() -> None:
    cfg = config_mod.load()
    b = SeleniumBroker(chromedriver=r"C:\TSE_Strategy\chromedriver.exe",
                       debugger_address="127.0.0.1:9333")
    eng = Engine(cfg, b, logger=lambda m: None)
    b.connect()

    # 1 clock: compare the bot clock with an internet time source (the OS clock is slow)
    drift = None
    try:
        import urllib.request as _u
        from email.utils import parsedate_to_datetime
        from datetime import timedelta as _td
        with _u.urlopen("https://www.google.com", timeout=6) as _r:
            real = parsedate_to_datetime(_r.headers.get("Date")) + _td(hours=3, minutes=30)
        drift = (now_tehran().replace(tzinfo=None) - real.replace(tzinfo=None)).total_seconds()
    except Exception:
        drift = None
    check("clock matches real Tehran time", drift is not None and abs(drift) <= 60,
          f"drift {drift:+.0f}s" if drift is not None else "no internet time")
    check("clock_offset_sec in config", float(cfg.get("system.clock_offset_sec", 0)) == 1800,
          str(cfg.get("system.clock_offset_sec")))

    # 2 order page + signal source + exclusions
    check("order page = tab 2", str(cfg.get("broker.order_page")) == "2", str(cfg.get("broker.order_page")))
    check("signal source ride_locked", str(cfg.get("signals.source")) == "ride_locked",
          str(cfg.get("signals.source")))
    check("خساپا excluded", "خساپا" in (cfg.get("portfolio.exclude_symbols") or []),
          str(cfg.get("portfolio.exclude_symbols")))
    check("buy enabled", bool(cfg.get("buy.enabled")), str(cfg.get("buy.enabled")))
    check("buy burst 09:00-09:05 / 30s",
          str(cfg.get("buy.window_start")) == "09:00" and str(cfg.get("buy.window_end")) == "09:05"
          and float(cfg.get("buy.interval_sec")) == 30,
          f"{cfg.get('buy.window_start')}-{cfg.get('buy.window_end')} @{cfg.get('buy.interval_sec')}s")
    check("preopen max_dist 0.5", float(cfg.get("buy.preopen_queue.max_dist_to_ceiling_pct")) == 0.5,
          str(cfg.get("buy.preopen_queue.max_dist_to_ceiling_pct")))

    # 3 live panel capabilities
    bound = b.bind_symbol("چکاوه")
    lo, hi = b.order_limits() if bound else (0, 0)
    check("bind_symbol + per-symbol آستانه حجم", hi > 0, f"چکاوه max={hi:,}")
    b.bind_symbol("وایرا")          # a locked stock has no depth rows; use a free one
    bid, ask = b.best_bid_ask()
    check("best_bid_ask readable", bid > 0 or ask > 0, f"bid={bid:.0f} ask={ask:.0f}")

    # 4 chunking maths (dry)
    d = eng.execute_exit(type("D", (), {"symbol": "وایرا", "qty": 900000, "reason": "target",
                                        "price_hint": 0.0, "blocked": "", "pnl_pct": 6.0})(), dry=True)
    ch = d.get("chunks") or []
    check("sell chunks by threshold", bool(ch) and max(ch) <= 300000, str(ch[:4]))
    r = eng.execute_buy("چکاوه", 250000, 21460, 0.79, dry=True)
    chb = r.get("chunks") or []
    check("buy chunks by threshold", bool(chb) and max(chb) <= 100000 and sum(chb) == r.get("filled"),
          f"{chb} sum={sum(chb):,} (live cash cap applied)")

    # 5 open-order guard
    led = Ledger(cfg.path("state_dir") / "ledger.jsonl")
    open_pos = [(p.symbol, int(p.qty)) for p in led.open_positions()]
    check("ledger open positions", bool(open_pos), str(open_pos))

    # 6 webapp
    import urllib.request
    try:
        html = urllib.request.urlopen("http://127.0.0.1:5077/", timeout=8).read().decode("utf-8", "ignore")
        check("webapp up", "TeseRobot" in html, f"{len(html)} bytes")
        check("chart rendered", html.count("<circle") >= 2, f"{html.count('<circle')} points")
    except Exception as exc:
        check("webapp up", False, str(exc)[:50])

    print(f"{'check':38s} {'result':6s} detail")
    for name, res, det in ROWS:
        print(f"{name:38s} {res:6s} {det}")
    fails = sum(1 for _, r, _ in ROWS if r == "FAIL")
    print(f"\n{len(ROWS)-fails}/{len(ROWS)} PASS")


if __name__ == "__main__":
    main()


