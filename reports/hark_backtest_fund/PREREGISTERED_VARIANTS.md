# Pre-registered variants — fundamentals strategy (written 2026-11-26 ~12:40 Tehran, BEFORE any backtest result was seen)

Data window for fundamental factors: rahavard365 EPS history (publish dates, point-in-time), first usable ~2024-08.
IS = 2024-08-01 .. 2025-09-30, OOS = 2025-10-01 .. 2026-11-25. Price-only control also run 2018-2026.
Signal on close of rebalance day t (only data published on or before t), orders executed at session t+1
(price = t+1 final price `pClosing` + 0.25 % slippage; no fill if t+1 is locked at the ceiling (buy) / floor (sell),
retry on following sessions; fill capped at 10 % of the session's traded value). Fees buy 0.3712 %, sell 0.88 %.
Equal weight, 10 holdings, monthly rebalance = every 21 sessions, rank buffer: a holding is kept while its rank <= 20.

Universe (all variants): TSE + IFB stocks with rahavard EPS history, traded >= 80 % of the last 60 sessions,
60-day average traded value >= 20 bn rial, not MANUAL_ONLY (افران، خساپا).
Quality gate G: latest TTM net profit > 0 (TTM when computable, else annualised cumulative EPS), and P/E TTM <= 40.

| id | rule |
|---|---|
| F1_QV | gate G, rank by earnings yield (E/P TTM) |
| F2_QVG | gate G, mean percentile rank of E/P, EPS YoY growth (same cumulative period), ROE (latest annual known) |
| F3_QVGM | F2 + 6-month price momentum (t-126 .. t-5) as a 4th equal-weight rank |
| F4_QVGM_2w | F3 rebalanced every 10 sessions |
| F5_QVGM_regime | F3, but when the equal-weight index is below its 100-session MA at t: no new buys and sell everything (cash) |
| F6_PEAD | event: day after a new EPS report with cumulative EPS YoY >= +30 % and EPS > 0, E/P TTM > 0, liquidity filter; hold 40 sessions; max 10 positions, rank by growth |
| F7_QVGM_flow | F3, excluding stocks in the bottom 20 % of 20-session net individual (حقیقی) inflow / avg value |
| P1_MOM (control) | no fundamentals: liquidity universe, rank by 6-month momentum, same mechanics, run 2018-2026 |

Missing factor values get the neutral rank 0.5. Benchmarks: TEDPIX buy&hold, equal-weight liquid-stock index,
live V3 (IS +7.8 %, OOS +155 %, maxDD 18.9/11.8 %).
Placebo: 200 random portfolios with the same schedule/mechanics drawn from (a) the gate-G universe, (b) the liquidity
universe; p = share of random runs with Sharpe >= the variant's.
Deployment gate (best variant): OOS return > 0 after fees, OOS Sharpe >= 1, maxDD <= 25 % in IS and OOS,
Sharpe above the equal-weight index in both IS and OOS, placebo (a) p < 0.10. All variants are reported.
