# V3 archive (2026-11-26)

V3 = ceiling-queue strategy (strategy_v3.py on top of strategy_v2.py; nightly queue_scan.py -> 6 picks, 10 slots,
9 % catastrophe stop, 15-session time exit, rank by individual buyer power). Live 2026-11-26 01:50 .. archived the
same day on the owner's order; F3_QVGM (STRATEGY_FUND_HARK.md) replaced it from 2026-11-28.

Files here are exact copies of what ran on the server:
- `strategy_v3.py`, `strategy_v2.py`, `queue_scan.py`
- `config_v3_live_20261126.json` — the complete config.json under which V3 ran (strategy.version = "v3")
- `STRATEGY_V3_HARK.md` — V3 rules and backtest

The live copies of strategy_v2.py / strategy_v3.py / queue_scan.py stay in the bot root: strategy_v2.live_params
still supplies the exit numbers for the legacy V3 position (آباد), and queue_scan.py still feeds the dashboard.
To bring V3 back: run `backups\pre_f3_<stamp>\rollback.ps1` (restores everything exactly), do not hand-edit.
