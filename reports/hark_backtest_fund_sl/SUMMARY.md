# F3_QVGM — stop-loss variants (research only, nothing deployed)

Date: 2026-11-27 (Tehran). Code: `warrobot/research/fund/bt_fund_sl.py` (engine), `rob_fund_sl.py` (robustness),
branch `hark/strategy-fund` in `/workspace/work/wr-v3` (local, not pushed). No server call was made: all data was
already in the sandbox (`/workspace/work/tsedata/panel_full.pkl`, EPS/ROE/Codal bundle). Not financial advice.

## Mechanics (same engine as the baseline, baseline reproduced exactly: IS +79.1 % / DD 22.7, OOS +385.8 % / DD 16.6)
- Trigger on the **daily close** (TSETMC total-return level, so capital raises/dividends never fire a stop):
  fixed = close <= cost basis x (1-x); trailing = close <= highest close since entry x (1-x).
- Sell order for the **next session**, filled at that session's close +- 0.25 % slippage, fees as before
  (buy 0.3712 %, sell 0.88 %), fill capped at 10 % of the session's traded value.
- **Floor queue (صف فروش)**: modelled from the daily price-limit data in the panel. A session locked at the floor
  all day (high <= floor) gives no fill; the sell is retried every following session until it fills.
- Re-entry (a) `cash`: proceeds wait for the next 21-session rebalance. (b) `replace`: after the stop sale fills,
  the best-ranked eligible name not held and not stopped this cycle is bought from the next session.
- Periods are the ones the baseline used: IS 2024-08-03..2025-09-30, OOS 2025-10-01..2026-11-25.
- Market closures in the data: 2025-06-11 -> 06-28 and 2026-02-25 -> 2026-05-19 (83 days). No stop can act inside a
  closure; the re-opening gap is taken in full (a real-world risk the stop cannot remove).

## Main table (full table incl. Sharpe, worst month, floor-lock retries: `table_sl.md`, raw: `results_sl.csv`)

| variant | IS ret % | IS DD % | OOS ret % | OOS DD % | triggers IS/OOS | win % IS/OOS | worst trade IS/OOS % | avg realised stop exit % |
|---|---|---|---|---|---|---|---|---|
| **baseline** | 79.1 | 22.7 | 385.8 | 16.6 | 0/0 | 65.0/84.2 | -25.8/-17.1 | – |
| fixed 8 % cash | 71.7 | 21.5 | 338.0 | 16.4 | 13/9 | 57.8/76.2 | -16.8/-15.6 | -11.8/-12.7 |
| fixed 8 % replace | 72.2 | 20.2 | 351.8 | 17.2 | 14/10 | 56.9/76.1 | -16.8/-15.8 | -11.9/-12.0 |
| fixed 10 % cash | 69.5 | 21.2 | 338.9 | 16.5 | 11/8 | 58.1/78.0 | -16.8/-17.8 | -13.3/-14.0 |
| fixed 10 % replace | 66.6 | 22.0 | 380.6 | 15.6 | 12/7 | 55.3/80.5 | -16.8/-17.8 | -13.5/-14.5 |
| fixed 12 % cash | 72.7 | 21.2 | 370.5 | 16.5 | 9/5 | 58.1/82.1 | -16.8/-17.8 | -14.6/-16.2 |
| fixed 12 % replace | 70.1 | 20.9 | 398.1 | 15.4 | 10/4 | 56.5/84.6 | -16.8/-17.8 | -14.8/-16.9 |
| **fixed 15 % cash** | 77.5 | 22.1 | 389.0 | 17.1 | 6/4 | 61.0/84.2 | -18.5/-19.5 | -16.8/-18.3 |
| fixed 15 % replace | 74.1 | 23.4 | 393.4 | 16.8 | 6/4 | 60.5/84.6 | -18.5/-19.5 | -16.8/-18.3 |
| fixed 20 % (both) | 79.1 | 22.7 | 385.8 | 16.6 | 1/0 | = baseline | -25.8/-17.1 | -24.6 |
| trail 15 % cash | 47.3 | 19.9 | 295.8 | 18.0 | 27/15 | 63.0/75.6 | -19.8/-21.6 | +3.4/+3.9 |
| trail 15 % replace | 52.2 | 21.4 | 330.8 | 18.5 | 27/16 | 62.7/74.0 | -19.8/-21.6 | +4.6/+2.7 |
| trail 20 % cash | 64.4 | 21.8 | 318.9 | 18.8 | 15/9 | 61.7/77.5 | -23.9/-20.8 | -7.3/-3.8 |
| trail 20 % replace | 65.0 | 23.0 | 372.7 | 16.5 | 15/9 | 60.0/78.0 | -23.9/-20.8 | -5.3/-3.6 |

CAGR (IS 14 months / OOS 14 months) is in `table_sl.md`; with this little history it is just the total return annualised,
not a meaningful long-run figure.

## What the numbers say
1. **No stop lowers portfolio max drawdown in a meaningful way** (best: 22.7 -> 20.2 IS, 16.6 -> 15.4 OOS, ~1-2 pp, and
   not consistent across variants). F3's drawdowns are market-wide (10 names falling together, plus closure gaps), and a
   single-name stop does not touch that.
2. **The stop cuts the single-name tail, but less than its label**: realised stop exits average 1-4 pp worse than the
   threshold (close trigger -> next-session fill, floor queues: up to 4-5 retry sessions on the 8-10 % stops).
   A "12 % stop" really exits around -15 to -17 %; a "15 % stop" around -17 to -18 %.
3. **Trailing stops are clearly harmful** (-14 to -32 pp IS, -13 to -90 pp OOS): they sell winners on normal pullbacks.
4. **Tight fixed stops (8-10 %) cost 7-13 pp IS and up to ~48 pp OOS** for a ~1 pp DD change.
5. **Re-entry**: `replace` looks better in the 2025-26 bull OOS, but in the price-only bear-market proxy (below) it was the
   worst choice every time (buys the next falling name). `cash` is the safer rule.

## Robustness (`robustness_f3.csv`, `robustness_p1.csv`)
- **Start-date sensitivity dwarfs the stop effect**: shifting the rebalance calendar by 5/10/15 sessions moves the
  baseline itself from +37.7 % to +85.1 % (IS) and from +252 % to +386 % (OOS). Stop variants do not rank consistently
  across offsets (e.g. fixed 12 % replace: best on the main calendar OOS, worst of all at +10 sessions OOS).
- Slippage 0.75 %: ordering unchanged; fixed 15 % cash stays within ~4 pp of baseline (IS 71.7 vs 73.3, OOS 376.6 vs 372.4).
- **Bear-market proxy** (no fundamentals exist before 2024-08, so the price-only momentum control P1_MOM, top-40 %
  liquidity, was run on 2018-2024): 2021-22 baseline -62.8 % / DD 71.8 %; fixed 10 % cash -57.4 % / 65.3 %; fixed 12 % cash
  -61.6 % / 68.6 %; fixed 15 % cash -63.6 % / 70.3 %; every `replace` variant was worse than no stop. Aug-2020..Jun-2021
  crash: baseline DD 61.0 %, fixed 10 % cash 55.3 %, fixed 15 % cash 61.2 %. -> In a real bear market a stop shaves a
  few points at best; it is not a crash protection.

## Recommendation
**Fixed 15 % from entry (close-based), re-entry (a): cash waits for the next rebalance.**
Cost: -1.6 pp IS, +3.2 pp OOS (i.e. ~0, within noise); 4-6 triggers a year; no floor-lock problem in the tests.
What it buys: a cap on a single-name blow-up (IS worst trade -25.8 % -> -18.5 %); in the bear proxy it neither helped
nor hurt. Treat it as **insurance against one name collapsing (bad report, suspension aftermath), not as drawdown
control**. If the goal is a real cut in portfolio drawdown, the lever is exposure (cash share / regime), not a stop —
and the tested regime filter (F5) failed IS.
Runner-up if more protection is wanted: fixed 12 % cash (-6 pp IS, -15 pp OOS); fixed 10-12 % cash were the only
variants that clearly helped in the bear proxy (DD -3 to -7 pp). Do **not** pick fixed 12 % replace for its OOS +12 pp: it rests on 4 trades and failed the bear proxy.

## Overfitting / data warnings
- Only ~28 months of point-in-time fundamentals (2024-08..2026-11), 6-16 stop events per period: every difference
  above is a handful of trades. Treat all ranks as noise except "trailing = bad" and "tight stops = expensive".
- No bear market in the F3 window (OOS was a +200 % TEDPIX bull run); the only bear evidence is the price-only proxy.
- 14 variants x 2 periods were tried; picking the best by OOS would be selection bias — the recommendation was chosen
  for the smallest change from baseline, not the best number.
- Fill model: close-price fills, a session counts as floor-locked only if it never traded above the floor; real
  queues can be worse (partial fills) — modelled only through the 10 % traded-value cap.

## How it would go live (sketch only — NOT deployed; needs owner approval)
**Where:** neither `sell_monitor.py` nor `exit_engine.py` should judge F3 positions. Both are intraday last-price
engines (sell_monitor uses TP 22 / SL 4 defaults; exit_engine is lot-based with V3 stops and a 15-session time exit),
and both currently skip F3 on purpose (sell_monitor: only legacy V3; exit_engine: MANUAL_ONLY + legacy). Letting a
second engine sell F3 names would re-create the old double-sell bug. The tested rule is close-based, so:
1. `strategy_fund.py --plan` (21:30 nightly): for every fund position (fund_ledger: not MANUAL_ONLY, not legacy V3),
   compute close / fee-inclusive cost (from the trade journal, else broker avg). If <= 0.85, put it in the plan's
   sell list with `reason="stop"`, and add the symbol to `state/fund_state.json` -> `stopped_cycle` (cleared at
   the next rebalance; a stopped name is not re-bought or topped up in the same cycle; its cash is not re-allocated
   until the rebalance = rule (a)).
2. `fund_orders.py` (08:47 and the later runs): sells first as today; a stop sell uses the floor-aware price
   (reuse `exit_engine.sell_limit("sl", ...)` = best bid else daily floor, clamped to the band) and is retried every
   run/session until filled (floor queue). Idempotency through the existing open-order / submitted-today checks.
3. Guards stay: `MANUAL_ONLY = {"افران","خساپا"}` never judged or sold by the stop (افران only by the existing
   excess-sale rule); legacy V3 (آباد) stays with exit_engine's 9 % / time exit; new config key
   `strategy.fund.stop_fixed_pct` default 0 (off).
4. Tests: unit tests in `tests/test_strategy_fund.py` (stop fires at -15 % close, not at -14.9 %; MANUAL_ONLY and legacy
   ignored; stopped name blocked until rebalance).
**Deploy steps (when approved):** backup `WarRobot\backups\pre_f3sl_<stamp>` (strategy_fund.py, fund_orders.py,
fund_ledger.py, config.json, state\fund_state.json) + `rollback.ps1`; deploy with `stop_fixed_pct=0`; run
`fund_live.py --plan` and `fund_orders.py --dry` (FLOW_DRY=1) and check the plan JSON; then set `stop_fixed_pct=15`,
dry run again; `pm2 save` before any restart; watch the first 21:30 plan + 08:47 run. `HARK_MCP_LIVE_TRADING=0` stays.

## Files
- `results_sl.csv`, `table_sl.md` — all variants, IS/OOS
- `robustness_f3.csv`, `robustness_p1.csv` — calendar offsets, slippage, bear proxy
- `curves/<variant>_<period>.csv`, `trades/<variant>_<period>.csv`
- `f3_sl_curves.png` (copy at `/workspace/work/f3_sl_curves.png`) — baseline vs fixed 15 % cash, fixed 12 % cash,
  fixed 12 % replace, IS and OOS, with drawdown panels
