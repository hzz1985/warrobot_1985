# War2 = F3_QVGM + fixed 15 % stop (F3 LIVE since Saturday 2026-11-28; stop approved 2026-11-27)

Owner decision 2026-11-26 22:53 Tehran: V3 is archived, F3_QVGM is the only automatic strategy, real orders from
the session of Saturday 2026-11-28 (1405-09-08). Not financial advice; numbers below are a backtest.

## Rules (pre-registered, `reports/hark_backtest_fund/PREREGISTERED_VARIANTS.md`)
| step | rule |
|---|---|
| universe | TSE + IFB stocks, traded on >= 80 % of the last 60 sessions, 60-session average value >= 20 bn rial; never افران / خساپا |
| gate | TTM net profit > 0 and 0 < P/E (TTM) <= 40 |
| factors | E/P (TTM), EPS YoY growth, ROE, 6-month momentum (t-126 .. t-5); each a percentile rank, missing = 0.5 |
| select | top 10 by the mean rank, equal weight; a holding stays while its rank <= 20 |
| rebalance | every 21 sessions; signal after the close, orders the next session (sells first, then buys) |

## War2 stop-loss (owner approved 2026-11-27 23:30 Tehran; research `reports/hark_backtest_fund_sl/SUMMARY.md`)
| item | rule |
|---|---|
| level | **fixed 15 % below the fee-inclusive entry cost** (broker average x 1.003712); no trailing stop, **no take-profit** |
| trigger | judged once a day on the **daily close** in the 21:30 plan (`fund_live.py --plan`); TSETMC price adjustments (capital raise, dividend) since the cost was recorded are taken out, so they never fire a stop |
| exit | next session `fund_orders.py` sells the whole position **first** (before the افران / rebalance sells and the buys), limit = best bid, else the daily floor (`exit_engine.sell_limit("stop_loss")`) |
| floor queue | an unfilled stop is re-sent on every run (08:47 .. 12:10) and every next day until the broker no longer shows the name; our own stop order above the current limit for >= 10 min is cancelled and re-placed |
| re-entry | **cash waits**: the stopped name is not bought or topped up again until the next 21-session rebalance, and no other name replaces it; at the rebalance it can come back only through the normal top-10 selection |
| scope | F3 positions only. افران / خساپا never judged; آباد keeps the V3 exits; `exit_engine` / `sell_monitor` never act on F3 positions |
| config | `strategy.fund.stop_fixed_pct` (15; 0 = pure F3), `strategy.fund.name` = "War2" (display); `strategy.version` stays `"fund"` |

Backtest (same engine as below): IS +77.5 % (F3 +79.1), OOS +389.0 % (F3 +385.8), max drawdown about the same;
worst single trade IS -25.8 % -> -18.5 %. Real exits average ~2-3 pp worse than the 15 % level (close trigger,
next-session fill, floor queues). It is **insurance against one name collapsing, not drawdown control**; only
4-6 stop events per period and no bear market in the data -> high overfitting risk.

Code: `war2_stop.py` (stop math + state), `strategy_fund.py` (rank/select), `fund_data.py` (rahavard365 EPS/ROE + optional Codal), `fund_live.py`
(nightly plan), `fund_ledger.py` (capital split / ownership), `fund_orders.py` (orders).

## Capital split (owner rules)
- **Total capital** = buying power + market value of **افران** + F3 stock holdings (+ open F3 buy orders).
  خساپا and the legacy V3 position (آباد) are NOT in the base.
- **1/3 of the total stays in افران.** The bot sells only the **excess above 1/3** (never below the target, at
  most one افران sale per Tehran day, only when the excess is >= max(50 M rial, 2 % of total)). The bot never
  buys افران by itself; `profit_transfer.py` (contract clause 6) still refills افران with realised profit but
  only up to the 1/3 target.
- **2/3 goes to the F3 picks, equally**: per name = (2/3 x total) / 10.
- **Recomputed on every run** (08:41 pre-open plan, 08:47 and 09:05 / 09:40 / 10:20 / 11:20 / 12:10): a
  deposit, a profitable sale or the افران sale raises the buying power and the next run sizes from the new total.
  Existing positions are not churned: a held F3 name is topped up only when it is >= 25 % of its target below
  it; no order is smaller than 50 M rial (`strategy.fund.min_trade_rial`, `topup_drift`).
- Sale proceeds: افران is a fixed-income ETF (T+1); money from a T+1 sale can buy T+2 stocks **the same day**
  (emofid / Farabi broker help pages), so the stock buys follow the افران sale within the same session.
  Stock sale money (T+2) cannot buy افران the same day.

## Positions at the switch
- **خساپا**: manual-only, never traded, never counted.
- **آباد** (opened by V3): kept as a *legacy V3* position (`state/fund_state.json` -> `legacy_v3`). It keeps
  the V3 exits in `exit_engine.py` (9 % catastrophe stop, 15-session time exit -> 2026-12-14) and is not one of
  the 10 F3 slots until the broker no longer shows it; then it leaves the list by itself. *Assumption made by
  Hark, owner may change it.*
- Every other broker position (besides افران / خساپا / legacy) is treated as an F3 position.

## Daily schedule (scheduler.py, Tehran)
| time | job | what |
|---|---|---|
| 21:30 every day | `fundplan` -> `fund_live.py --plan` | War2 stop judgement on the close + ranks the universe, writes `state/fund_plan.json` (rebalance every 21 sessions, else hold) |
| 08:41 | `morning` -> `morning_orders.py` | V3-legacy exits + `order_candidates.prepare()` = F3 buys the cash allows (pre-open, 08:45:01) |
| 08:47 | `fundbuy` -> `fund_orders.py` | War2 stop sells first, then the افران excess, re-read buying power, buy the F3 names |
| 09:05 .. 12:10 | `fundbuy2..6` | retry unfilled War2 stops, buy what is still missing as buying power arrives (idempotent) |
| 10:00 | `rotate` -> `rebalance.py` | **disabled** in fund mode (it used to `cancel_all()`) |
| 21:25 | `sellrev` -> `sell_monitor.py` | judges legacy V3 positions only; never F3 / افران / خساپا |

## Backtest (`reports/hark_backtest_fund/`)
| period | return | max drawdown | Sharpe |
|---|---|---|---|
| IS 2024-09 .. 2025-09 | +79.1 % | 22.7 % | 2.62 |
| OOS 2025-10 .. 2026-11 | +385.8 % | 16.6 % | 7.32 |

## Caveats (read before trusting the numbers)
- Fundamentals exist only from **2024-08**: ~2 years of history, one market regime.
- **No bear market** in the data; the equal-weight index itself rose +272 % OOS. F3 beat it, but by less than
  the headline suggests, and it did not pass every pre-registered gate.
- The **momentum leg** carries most of the risk (P1_MOM alone: OOS +200 %); a momentum crash would hit F3.
- **Start-date sensitivity**: with a 21-session rebalance the result depends on which day the cycle starts
  (`robustness_F3_QVGM.csv`).
- Live data is rahavard365 EPS/ROE (+ Codal when available); a data gap makes the plan "hold" (no trades).
- Small account: per-name size is ~150 M rial today, so fees and tick sizes matter more than in the backtest.

## Config (`config.json` -> `strategy`)
`version: "fund"` (keeps the v2/v3 exit numbers for the legacy position), `v3.enabled: false` (archived,
`archive/v3_20261126/`), `fund: {enabled: true, mode: "replace", live_orders: true, manage_afran: true,
afran_share: 1/3, n_hold: 10, buffer: 20, rebalance_sessions: 21, pe_max: 40, min_trade_rial: 5e7,
topup_drift: 0.25, afran_min_excess_pct: 0.02, name: "War2", stop_fixed_pct: 15}`.

## Rollback
`powershell -ExecutionPolicy Bypass -File "<WarRobot>\backups\pre_f3_<stamp>\rollback.ps1"` restores every
file and the state of V3 exactly as it was on 2026-11-26 and restarts warsched + warrobot.
Quick stop without rollback: set `strategy.fund.live_orders` to `false` (rehearsal only, no orders).
War2 stop off without rollback: `strategy.fund.stop_fixed_pct` = 0 (back to pure F3). Full War2 rollback:
`backups\pre_war2_<stamp>\rollback.ps1`.
