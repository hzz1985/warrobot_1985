"""Fundamentals strategy ("fund") — quality / value / growth / momentum stock selection, rebalanced on a
fixed session schedule.  Independent of the ceiling-queue setup (V1/V2/V3).

Rules (pre-registered, reports/hark_backtest_fund/PREREGISTERED_VARIANTS.md):
  universe : TSE + IFB stocks with EPS history, traded on >= 80 % of the last 60 sessions,
             60-session average traded value >= min_value_rial, never MANUAL_ONLY (افران، خساپا)
  gate G   : TTM net profit > 0 and 0 < P/E TTM <= pe_max
  factors  : E/P (TTM), EPS YoY growth (same cumulative period), ROE (latest annual / Codal TTM),
             6-month momentum (t-126 .. t-5 sessions); each a percentile rank 0..1, missing -> 0.5
  select   : top n_hold by the mean rank; a holding stays while its rank <= buffer
  regime   : (optional) equal-weight index below its regime_ma-session mean -> target = cash
  orders   : signal after the close of day t, orders for session t+1; sells first, then buys,
             equal weight = equity / n_hold per new name

Live use (default OFF): config.json  strategy.fund.enabled = true.  `python strategy_fund.py --plan`
writes state/fund_plan.json (target portfolio, buy list, sell list) and never places an order;
fund_orders.py executes a plan.  Any data gap fails SAFE: no new buys, no sells (holdings untouched).
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path

NAN = float("nan")
MANUAL_ONLY = {"افران", "خساپا"}


@dataclass
class FundConfig:
    variant: str = "F3_QVGM"
    factors: tuple = ("ey", "yoy", "roe", "mom")
    n_hold: int = 10
    buffer: int = 20
    rebalance_sessions: int = 21
    min_value_rial: float = 20e9
    min_traded_frac: float = 0.8
    pe_max: float = 40.0
    mom_lb: int = 126
    mom_skip: int = 5
    regime_ma: int = 0                  # 0 = off
    flow_bottom_pct: float = 0.0        # F7: drop the bottom x of 20-session net individual inflow
    mode: str = "rank"                  # rank | pead
    pead_yoy: float = 0.30
    pead_hold: int = 40
    liq_cap: float = 0.10               # max share of a session's traded value per fill
    slippage: float = 0.0025
    fee_buy: float = 0.003712
    fee_sell: float = 0.0088


VARIANTS: dict[str, FundConfig] = {
    "F1_QV": FundConfig(variant="F1_QV", factors=("ey",)),
    "F2_QVG": FundConfig(variant="F2_QVG", factors=("ey", "yoy", "roe")),
    "F3_QVGM": FundConfig(variant="F3_QVGM"),
    "F4_QVGM_2w": FundConfig(variant="F4_QVGM_2w", rebalance_sessions=10),
    "F5_QVGM_regime": FundConfig(variant="F5_QVGM_regime", regime_ma=100),
    "F6_PEAD": FundConfig(variant="F6_PEAD", mode="pead"),
    "F7_QVGM_flow": FundConfig(variant="F7_QVGM_flow", flow_bottom_pct=0.2),
    "P1_MOM": FundConfig(variant="P1_MOM", factors=("mom",), pe_max=0.0),   # control: no fundamentals
}


def pct_rank(vals: list[float]) -> list[float]:
    """percentile rank in [0, 1] (ties averaged); NaN -> 0.5 (neutral)."""
    idx = [i for i, v in enumerate(vals) if not (v is None or (isinstance(v, float) and math.isnan(v)))]
    out = [0.5] * len(vals)
    if not idx:
        return out
    srt = sorted(idx, key=lambda i: vals[i])
    n = len(srt)
    k = 0
    while k < n:
        j = k
        while j + 1 < n and vals[srt[j + 1]] == vals[srt[k]]:
            j += 1
        r = ((k + j) / 2.0) / (n - 1) if n > 1 else 0.5
        for t in range(k, j + 1):
            out[srt[t]] = r
        k = j + 1
    return out


def gate(row: dict, cfg: FundConfig) -> bool:
    """liquidity + quality gate for one candidate row."""
    if row.get("sym") in MANUAL_ONLY:
        return False
    if not row.get("liquid"):
        return False
    if cfg.pe_max and cfg.pe_max > 0:
        np_ttm, mcap = row.get("np_ttm"), row.get("mcap")
        if np_ttm is None or mcap is None or math.isnan(np_ttm) or math.isnan(mcap) or np_ttm <= 0 or mcap <= 0:
            return False
        if mcap / np_ttm > cfg.pe_max:
            return False
    return True


def rank(rows: list[dict], cfg: FundConfig) -> list[dict]:
    """rows: [{sym, liquid, np_ttm, mcap, yoy, roe, mom, flow}] -> eligible rows sorted best-first with
    `score` and `rank` (1 = best)."""
    el = [dict(r) for r in rows if gate(r, cfg)]
    for r in el:
        r["ey"] = (r["np_ttm"] / r["mcap"]) if r.get("mcap") and r.get("np_ttm") is not None \
            and not math.isnan(r.get("np_ttm", NAN)) and r["mcap"] > 0 else NAN
    if cfg.flow_bottom_pct and el:
        fl = [r.get("flow", NAN) for r in el]
        ok = [v for v in fl if not math.isnan(v)]
        if ok:
            cut = sorted(ok)[int(len(ok) * cfg.flow_bottom_pct)]
            el = [r for r in el if math.isnan(r.get("flow", NAN)) or r["flow"] >= cut]
    if not el:
        return []
    ranks = {f: pct_rank([r.get(f, NAN) for r in el]) for f in cfg.factors}
    for i, r in enumerate(el):
        r["score"] = sum(ranks[f][i] for f in cfg.factors) / len(cfg.factors)
    el.sort(key=lambda r: (-r["score"], r["sym"]))
    for i, r in enumerate(el, 1):
        r["rank"] = i
    return el


def select(ranked: list[dict], held: list[str], cfg: FundConfig) -> list[str]:
    """target names: holdings still within the buffer, then the best new names up to n_hold."""
    pos = {r["sym"]: r["rank"] for r in ranked}
    keep = [s for s in held if s in pos and pos[s] <= cfg.buffer and s not in MANUAL_ONLY]
    keep = sorted(keep, key=lambda s: pos[s])[:cfg.n_hold]
    out = list(keep)
    for r in ranked:
        if len(out) >= cfg.n_hold:
            break
        if r["sym"] not in out:
            out.append(r["sym"])
    return out


def load_config(path) -> tuple[dict, FundConfig]:
    """config.json strategy.fund -> (raw dict, FundConfig).  Unknown / missing -> defaults (enabled False)."""
    try:
        raw = (json.loads(Path(path).read_text(encoding="utf-8")).get("strategy") or {}).get("fund") or {}
    except Exception:
        raw = {}
    base = VARIANTS.get(raw.get("variant") or "F3_QVGM", VARIANTS["F3_QVGM"])
    over = {k: raw[k] for k in ("n_hold", "buffer", "rebalance_sessions", "min_value_rial", "pe_max",
                                "regime_ma", "liq_cap") if raw.get(k) is not None}
    return raw, replace(base, **over)


if __name__ == "__main__":       # the live entry point lives in fund_live.py (keeps this module import-light)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fund_live
    raise SystemExit(fund_live.main(sys.argv[1:]))
