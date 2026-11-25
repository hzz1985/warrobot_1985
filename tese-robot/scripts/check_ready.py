# -*- coding: utf-8 -*-
"""Readiness check: is the robot ready for the next session?

Exit code 0 only if every check passes. Designed to be run the night before and
again at 08:15, so a silent data/model problem cannot become a silent no-trade
day (or, worse, a trade on stale picks).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tsebot import config as config_mod  # noqa: E402
from tsebot.calendar_ir import TradingCalendar, from_jalali, jalali_str, now_tehran  # noqa: E402


def main() -> int:
    cfg = config_mod.load()
    cal = TradingCalendar(holidays_path=cfg.path("state_dir") / "holidays.json")
    today = now_tehran().date()
    tomorrow = cal.next_trading_day(today)
    rows: list[tuple[str, bool, str]] = []

    # 1) data freshness
    dates = []
    for f in sorted(cfg.path("market_data_dir").glob("*_daily.csv"))[:40]:
        try:
            dates.append(pd.read_csv(f, usecols=["date"])["date"].astype(str).max())
        except Exception:
            continue
    last = max(dates) if dates else ""
    last_g = from_jalali(last) if last else None
    fresh = bool(last_g and (today - last_g).days <= 3)
    rows.append(("data", fresh, f"last bar {last} ({last_g}) vs today {today}"))

    # 2) model
    mp = cfg.get("signals.model_path")
    if not Path(mp).is_absolute():
        mp = config_mod.ROOT / mp
    rows.append(("model", Path(mp).exists(), str(mp)))

    # 3) day features for the last bar
    feat = config_mod.ROOT / "features" / f"feature_day_{last}.csv"
    n_feat = 0
    if feat.exists():
        try:
            n_feat = int(pd.read_csv(feat, usecols=["symbol"])["symbol"].nunique())
        except Exception:
            n_feat = -1
    rows.append(("features", feat.exists() and n_feat > 0,
                 f"{feat.name} symbols={n_feat}"))

    # 4) picks for tomorrow
    picks_p = cfg.path("picks_dir") / "today_picks.json"
    ok_picks, detail = False, "missing"
    if picks_p.exists():
        try:
            d = json.loads(picks_p.read_text(encoding="utf-8"))
            for_date = str(d.get("for_date_g") or "")
            n = len(d.get("picks") or [])
            ok_picks = (for_date == tomorrow.isoformat()) and n > 0
            detail = (f"source={d.get('source')} for={for_date} picks={n} "
                      f"model={d.get('model')} min_prob={d.get('min_prob')}")
        except Exception as exc:
            detail = f"unreadable: {exc}"
    rows.append(("picks_for_tomorrow", ok_picks, detail))

    # 5) kill switch
    stop = cfg.path("state_dir") / "STOP"
    rows.append(("no_kill_switch", not stop.exists(), str(stop)))

    # 6) ledger
    led = cfg.path("state_dir") / "ledger.jsonl"
    rows.append(("ledger", True, f"{led.name} exists={led.exists()}"))

    width = max(len(r[0]) for r in rows)
    all_ok = True
    print(f"TeseRobot readiness — now={now_tehran():%Y-%m-%d %H:%M}  next session={tomorrow} ({jalali_str(tomorrow)})")
    print("-" * 78)
    for name, ok, detail in rows:
        all_ok &= ok
        print(f"{name:<{width}}  {'PASS' if ok else 'FAIL'}  {detail}")
    print("-" * 78)
    print("READY" if all_ok else "NOT READY")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
