# AGENTS.md — read this first (DeepSeek Harness / any coding agent working on WarRobot)

Last update: 2026-11-26 (Hark). This file wins over older docs (README.md, docs/RUNBOOK.md, docs/TRADING_SPEC.md)
wherever they disagree about the strategy.

## 1. What runs live now
- **Strategy: F3_QVGM (fundamentals) is the ONLY automatic strategy**, real orders from Saturday 2026-11-28.
  Full rules, backtest and caveats: `STRATEGY_FUND_HARK.md` (this folder).
- **V3 (ceiling-queue) is ARCHIVED**: `config.json strategy.v3.enabled=false`; code + config kept in
  `archive/v3_20261126/`. Do not delete it, do not re-enable it without the owner's explicit order.
  `queue_scan.py` may still write `state/candidates.csv` for the dashboard; nothing buys from it.
- F3 rules: liquid universe (>= 80 % of 60 sessions traded, avg value >= 20 bn rial); TTM profit > 0;
  0 < P/E <= 40; rank = mean percentile of E/P, EPS YoY growth, ROE, 6-month momentum; top 10 equal weight;
  hold while rank <= 20; rebalance every 21 sessions.

## 2. Money rules (owner, 2026-11-26)
- Total capital = buying power + افران value + F3 holdings. **1/3 stays in افران, 2/3 split equally over the 10
  F3 picks.** Recomputed on every run (deposit / profitable sale / افران sale -> bigger next buys).
- The bot **sells only the افران excess above 1/3** (never below), first, then buys the F3 names as buying power
  arrives (افران is T+1, so its sale money buys stocks the same day). The bot never buys افران by itself;
  profit_transfer refills it only up to 1/3.
- Minimum order 50 M rial; an existing F3 name is topped up only when >= 25 % below its target (no churn).
- **خساپا: manual-only.** Never sold, never bought, not in the sizing base.
- **آباد (opened by V3): legacy V3 position** (`state/fund_state.json` `legacy_v3`). Keeps the V3 exits in
  `exit_engine.py` (9 % stop, 15-session time exit -> 2026-12-14); not an F3 slot until it is gone.

## 3. Where the code is
| file | role |
|---|---|
| `strategy_fund.py` | ranking / selection (pure) |
| `fund_data.py`, `fund_fetch.py` | rahavard365 EPS/ROE (+ Codal) snapshots |
| `fund_live.py --plan` | nightly plan -> `state/fund_plan.json` (21:30 every day, job `fundplan`) |
| `fund_ledger.py` | capital split, who owns which position, legacy V3 list |
| `fund_orders.py` | sells the افران excess / rebalance sells, then buys (jobs `fundbuy`..`fundbuy6`); `--dry`, `--plan-only` |
| `order_candidates.py` | in fund mode `prepare()` returns the F3 buys (used by `morning_orders.py` at 08:45:01) |
| `exit_engine.py` | judges ONLY legacy V3 positions in fund mode |
| `rebalance.py` | 10:00 rotation disabled in fund mode (no more `cancel_all`) |
| `sell_monitor.py` | never touches F3 / افران / خساپا in fund mode |
| `tests/test_strategy_fund.py` | unit tests (`python -m unittest tests.test_strategy_fund`) |
Logs: `state/fund_orders.jsonl`, `state/fund_run.json`, `state/fund_log.jsonl`, `state/orders_log.jsonl`.
Kill switch without rollback: `config.json strategy.fund.live_orders=false` (fund orders become FLOW_DRY rehearsals).

## 4. Safety rules for every agent (no exceptions)
1. **Before any change**: full backup to `backups\<name>_<timestamp>` + a `rollback.ps1` that restores it exactly.
2. **Test with `FLOW_DRY=1`** (`$env:FLOW_DRY="1"`; `python fund_orders.py --dry --force`) before anything live.
   Never run an order script without FLOW_DRY=1 unless the owner asked for that exact live order.
3. `pm2 save` **before** restarting (`pm2 restart warsched warrobot --update-env`), then check `pm2 ls` = online.
4. Never print, change or rotate the hark-mcp token; never touch `hark-tunnel`; `HARK_MCP_LIVE_TRADING` stays `0`.
5. **Low integrity**: the WarRobot folder carries a `Mandatory Label\Low` ACE; an exe inside it (e.g. a venv
   `python.exe`) runs at Low integrity and cannot use pm2 (EPERM). Use the system Python
   `C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe`; do not change labels without the owner.
6. Never push to `main`; work on a `hark/*` or feature branch, open a draft PR (repo `hzz1985/warrobot_1985`).
7. The server clock is US **Pacific**; all trading times are **Tehran** (`market_cal.now_tehran()`). A Tehran
   session spans two Pacific dates (date flips at 10:30 Tehran) — key daily guards by the Tehran date.
8. If a step fails twice, stop and report.

## 5. Docs
`STRATEGY_FUND_HARK.md` (F3), `archive/v3_20261126/` (V3), `docs/RUNBOOK.md` (operations), `docs/TRADING_SPEC.md`
(contract; clauses 4-6 are amended by the F3 rules above), `docs/FIXES_HARK.md`, backups in `backups\`.
