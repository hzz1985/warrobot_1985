# V3.1 fundamental-quality variants — declared 2026-11-26 02:45 IRST, before any Codal-based backtest/OOS run

Base: V3 live (V2 entry/exit, exclude known loss-makers via rahavard EPS, rank by individual buyer power).
All Codal features are point-in-time: a report counts from its Codal PublishDateTime (date <= signal day; entry is next session).
Unknown data => filter passes (fail-open), same as live.

Features (latest Codal ن-10 statement known on signal day, one file gives cumulative current period,
same period last year and last full fiscal year):
- pe_c      = market cap / TTM net profit  (TTM = cur + lastFY - prevYearSamePeriod; annual => FY)
- roe       = TTM net profit / total equity (latest balance sheet)
- npm       = TTM net profit / TTM operating revenue
- np_yoy    = cumulative net profit vs same period last year ((cur-prev)/|prev|)
- sales_yoy = latest ن-30 monthly report: fiscal-YTD sales vs same period last year
- capraise_reval = non-cash capital increase (rahavard `is_reserve`: from reserves / revaluation surplus; rahavard does not split the two) with registration date in the last 120 days (rahavard capital history). [wording fixed 02:50 before any run: revaluation alone is not separable in the free source]

Variants (exclude rule on top of V3):
- Q1  pe_c > 100 (or TTM loss)            [risk guard]
- Q2  pe_c > 50  (or TTM loss)
- Q3  roe < 5 %
- Q4  roe < 3 %                            [risk guard]
- Q5  npm < 5 % (when revenue known)
- Q6  np_yoy < -50 %
- Q7  sales_yoy < 0
- Q8  capraise_reval in last 120 days
- Q9  combo guard: Q1 or Q4 or Q6
- Q10 Q9 + Q7
Decision rule (fixed now): a variant is recommended only if it improves IS (return or DD) AND does not hurt OOS
total return by more than 10 % relative while lowering DD; pure risk guards are reported with their cost.

## Data-source amendment (03:40 IRST, before any result with real coverage was looked at)
search.codal.ir throttles this server to ~30 requests then HTTP 429 for 30+ min, so the full Codal crawl needs ~10 h.
Rules stay identical; each feature uses the best point-in-time source available on the signal day:
- pe      : Codal TTM P/E when a Codal statement is known, else rahavard365 TTM P/E (same Codal EPS data, integer EPS)
- roe     : Codal TTM ROE, else rahavard365 annual "Return on Equity" (published at the AGM date -> PIT by that date)
- np_yoy  : Codal cumulative profit YoY, else rahavard365 cumulative EPS YoY (features_v3 'growth')
- npm, sales_yoy : Codal only (coverage reported)
- capraise: rahavard365 capital history (full coverage)
Results are reported for the hybrid ("H") features; Codal-only coverage is reported separately.
