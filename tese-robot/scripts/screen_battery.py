"""Screening battery: which parameter improves the strategy, per profit target?

Runs the REAL backtester (same fill and exit model) once per (feature, target) with
a top-quartile filter and records win rate / expectancy, so the comparison is
apples-to-apples with the baseline. Resumable: finished rows are skipped.

Usage:  python scripts/screen_battery.py
Output: reports/screen_battery.csv   (+ stdout table)
"""
import itertools
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot")
sys.path.insert(0, str(ROOT / "src"))

from tsebot import config as config_mod                      # noqa: E402
from tsebot.backtest import load_panel, run_backtest        # noqa: E402

OUT = ROOT / "reports" / "screen_battery.csv"
TARGETS = (6.0, 9.0, 12.0)
START = "1405-01-01"
FEATURES = [
    "avg_trade_value", "TradeCount", "log_value", "vwap", "close_pos",
    "range_pct", "value_ratio_20", "trades_ratio_20", "atv_ratio_20",
    "close_pos_ma5", "up_days_5",
]


def norm(s: str) -> str:
    return (str(s).replace("ي", "ی").replace("ك", "ک").replace("\u200c", "")
            .replace(" ", "").strip())


def load_flow() -> dict:
    """(symbol, date) -> flow feature dict, built from the downloaded trade tables."""
    frames = []
    for f in sorted((ROOT / "data" / "trades").glob("*.csv")):
        if f.stat().st_size == 0:
            continue
        try:
            d = pd.read_csv(f)
        except Exception:
            continue
        if d.empty or "Symbol" not in d.columns:
            continue
        d["date"] = f.stem
        frames.append(d[["date", "Symbol", "TradeCount", "Volume", "Value",
                         "High", "Low", "Close"]])
    p = pd.concat(frames, ignore_index=True)
    p["symbol"] = p["Symbol"].map(norm)
    for c in ("TradeCount", "Volume", "Value", "High", "Low", "Close"):
        p[c] = pd.to_numeric(p[c], errors="coerce")
    p = p[(p["Value"] > 0) & (p["TradeCount"] > 0) & (p["Volume"] > 0)].copy()
    p["avg_trade_value"] = p["Value"] / p["TradeCount"]
    p["vwap"] = p["Value"] / p["Volume"]
    p["close_pos"] = (p["Close"] - p["Low"]) / (p["High"] - p["Low"]).replace(0, float("nan"))
    p["range_pct"] = (p["High"] - p["Low"]) / p["Close"] * 100
    import numpy as _np
    p["log_value"] = _np.log10(p["Value"].clip(lower=1))
    p = p.sort_values(["symbol", "date"])
    g = p.groupby("symbol", group_keys=False)
    p["value_ratio_20"] = g["Value"].transform(lambda s: s / s.rolling(20, min_periods=5).mean())
    p["trades_ratio_20"] = g["TradeCount"].transform(lambda s: s / s.rolling(20, min_periods=5).mean())
    p["atv_ratio_20"] = g["avg_trade_value"].transform(lambda s: s / s.rolling(20, min_periods=5).mean())
    p["close_pos_ma5"] = g["close_pos"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    p["up_days_5"] = g["Close"].transform(lambda s: (s.diff() > 0).rolling(5, min_periods=3).sum())
    recs = p[["symbol", "date"] + FEATURES].to_dict("records")
    out = {(r["symbol"], r["date"]): {f: r[f] for f in FEATURES} for r in recs}
    return out, p


def main() -> None:
    flow, raw = load_flow()
    print(f"flow rows: {len(flow):,}")
    q75 = {f: float(pd.to_numeric(raw[f], errors="coerce").quantile(0.75)) for f in FEATURES}

    done = set()
    if OUT.exists():
        try:
            old = pd.read_csv(OUT)
            done = {(r["feature"], float(r["target"])) for _, r in old.iterrows()}
        except Exception:
            pass

    panel = load_panel(config_mod.load().path("market_data_dir"))
    print(f"panel symbols: {len(panel)}", flush=True)
    rows = []
    for target in TARGETS:
        cfg = config_mod.load()
        cfg.raw.setdefault("backtest", {})["target_pct"] = target
        cfg.raw["backtest"]["start"] = START
        base = run_backtest(cfg, panel=panel, fill_model="open_if_not_locked", top_n=3)
        bs = base.stats
        rows.append({"feature": "BASELINE", "target": target, "threshold": None,
                     "trades": bs.get("trades", 0), "win": bs.get("win_rate_pct"),
                     "avg": bs.get("avg_pct"), "pf": bs.get("profit_factor")})
        print(rows[-1], flush=True)
        for f in FEATURES:
            if (f, target) in done:
                continue
            cfg = config_mod.load()
            cfg.raw.setdefault("backtest", {})["target_pct"] = target
            cfg.raw["backtest"]["start"] = START
            try:
                r = run_backtest(cfg, panel=panel, fill_model="open_if_not_locked", top_n=3,
                                 flow_features=flow, feature_filters={f: q75[f]})
                st = r.stats
                row = {"feature": f, "target": target, "threshold": round(q75[f], 3),
                       "trades": st.get("trades", 0), "win": st.get("win_rate_pct"),
                       "avg": st.get("avg_pct"), "pf": st.get("profit_factor")}
            except Exception as exc:
                row = {"feature": f, "target": target, "threshold": round(q75[f], 3),
                       "trades": 0, "win": None, "avg": None, "pf": None}
                print("ERR", f, target, str(exc)[:100], flush=True)
            rows.append(row)
            pd.DataFrame(rows).to_csv(OUT, index=False, encoding="utf-8")
            print(row, flush=True)

    tbl = pd.DataFrame(rows)
    tbl.to_csv(OUT, index=False, encoding="utf-8")
    print(tbl.to_string(index=False))
    print(f"\nwritten: {OUT}")


if __name__ == "__main__":
    main()





