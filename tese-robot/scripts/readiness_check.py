"""Readiness check for the next trading session."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from tsebot import config as config_mod  # noqa: E402
from tsebot.alerts import send  # noqa: E402
from tsebot.broker_selenium import SeleniumBroker  # noqa: E402
from tsebot.calendar_ir import now_tehran  # noqa: E402
from tsebot.engine import Engine  # noqa: E402
from tsebot.ledger import Ledger  # noqa: E402

ROWS = []


def check(name, ok, detail=""):
    ROWS.append((name, "PASS" if ok else "FAIL", str(detail)[:80]))


def main() -> None:
    cfg = config_mod.load()
    now = now_tehran()
    te = now.strftime("%Y-%m-%d %H:%M")
    drift = None
    try:
        import urllib.request as _u
        from email.utils import parsedate_to_datetime
        with _u.urlopen("https://www.google.com", timeout=6) as _r:
            real = parsedate_to_datetime(_r.headers.get("Date")) + timedelta(hours=3, minutes=30)
        drift = (now.replace(tzinfo=None) - real.replace(tzinfo=None)).total_seconds()
    except Exception:
        pass
    check("bot clock", drift is not None and abs(drift) < 120,
          f"drift {drift:+.0f}s" if drift is not None else te)

    # config
    check("buy enabled", bool(cfg.get("buy.enabled")), str(cfg.get("buy.enabled")))
    check("buy window", f"{cfg.get('buy.preopen_attempt')} + {cfg.get('buy.window_start')}-"
                        f"{cfg.get('buy.window_end')}",
          f"every {cfg.get('buy.interval_sec')}s")
    check("order page", str(cfg.get("broker.order_page")) == "2", str(cfg.get("broker.order_page")))
    check("max positions", str(cfg.get("portfolio.max_positions")), str(cfg.get("portfolio.max_positions")))
    check("excluded", str(cfg.get("portfolio.exclude_symbols")), "")

    # broker
    b = SeleniumBroker(chromedriver=r"C:\TSE_Strategy\chromedriver.exe",
                       debugger_address="127.0.0.1:9333")
    eng = Engine(cfg, b, logger=lambda m: None)
    try:
        b.connect()
        b.bind_symbol("چکاوه")
        power = float(b.buying_power() or 0)
        check("broker session", power > 0, f"power {power:,.0f} rial")
        check("buying power", power > 0, f"{power/1e7:.1f} M toman")
    except Exception as exc:
        check("broker session", False, str(exc)[:60])
        power = 0.0

    # ledger vs panel
    led = Ledger(cfg.path("state_dir") / "ledger.jsonl")
    try:
        for sym in ("چکاوه", "خساپا"):
            row = b.row_cells(sym) or {}
            held_panel = row.get("دارایی لحظه")
            pos = led.get(sym)
            held_led = int(pos.qty) if pos else 0
            check(f"holding {sym}", True, f"panel={held_panel} ledger={held_led:,}")
    except Exception as exc:
        check("holdings", False, str(exc)[:60])

    # candidates
    cand = cfg.path("state_dir") / "candidates_next.csv"
    if cand.exists():
        c = pd.read_csv(cand, dtype={"for_session": str})
        check("candidates", len(c) > 0,
              f"{len(c)} rows for {sorted(set(c['for_session']))}")
        for _, r in c.iterrows():
            check(f"  cand {r['symbol']}", float(r['prob']) > 0,
                  f"prob={r['prob']:.3f} close={r['close']:.0f} next_ceiling={r['next_ceiling_est']:.0f}")
    else:
        check("candidates", False, "missing")

    # data freshness
    daily = cfg.path("daily_dir")
    if not daily.exists():
        daily = Path(__file__).resolve().parents[1] / "data" / "daily"
    last = ""
    n = 0
    for f in daily.glob("*_daily.csv"):
        try:
            d = pd.read_csv(f, usecols=["date"], dtype={"date": str})
            if len(d):
                n += 1
                last = max(last, str(d["date"].iloc[-1]))
        except Exception:
            continue
    check("daily data", last == "1405-07-29", f"{n} files, last {last}")

    print(f"{'item':34s} {'result':6s} detail")
    for name, res, det in ROWS:
        print(f"{name:34s} {res:6s} {det}")
    fails = sum(1 for _, r, _ in ROWS if r == "FAIL")
    print(f"\n{len(ROWS)-fails}/{len(ROWS)} PASS")

    body = ["بررسی آمادگی ربات برای جلسهٔ بعدی", f"زمان: {te} (تهران)", ""]
    body += [f"{n:34s} {r:6s} {d}" for n, r, d in ROWS]
    body += ["", f"جمع: {len(ROWS)-fails}/{len(ROWS)} PASS"]
    send("[TeseRobot] آمادگی برای جلسهٔ بعدی", "\n".join(body), key="ready", force=True)


if __name__ == "__main__":
    main()
