"""Stage 1 — build order-flow features from downloaded trade tables and screen them.

Inputs
  data/trades/<jalali>.csv   one row per instrument per session (TradeCount, Volume,
                             Value, OHLC) downloaded from the exchange
  data/daily/<symbol>.csv    daily bars (for forward returns)
  data/train/queue_train.csv historical signals + labels

Output
  reports/flow_screen.csv    win rate of the top quartile of each feature at
                             net targets 6 / 9 / 12 percent (stop -3, fees 2)
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot")
BE = 1.0089          # breakeven factor (sell-side fee on top of the entry price)
STOP_NET = 3.0
HOLD = 10


def norm(s: str) -> str:
    return (str(s).replace("ي", "ی").replace("ك", "ک").replace("\u200c", "")
            .replace(" ", "").strip())


def load_trade_panel() -> pd.DataFrame:
    frames = []
    for f in sorted((ROOT / "data" / "trades").glob("*.csv")):
        if f.stat().st_size == 0:
            continue
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        if df.empty or "Symbol" not in df.columns:
            continue
        df["date"] = f.stem
        frames.append(df[["date", "Symbol", "InsCode", "TradeCount", "Volume",
                          "Value", "Open", "High", "Low", "Close", "Last",
                          "PreviousClose", "ChangePct"]])
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates(["date", "Symbol"])


def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    df = panel.copy()
    df["symbol"] = df["Symbol"].astype(str)
    for c in ("TradeCount", "Volume", "Value", "High", "Low", "Close", "Open"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[(df["Value"] > 0) & (df["TradeCount"] > 0) & (df["Close"] > 0)]
    df["avg_trade_value"] = df["Value"] / df["TradeCount"]
    df["vwap"] = df["Value"] / df["Volume"].replace(0, np.nan)
    df["close_pos"] = ((df["Close"] - df["Low"]) /
                       (df["High"] - df["Low"]).replace(0, np.nan))
    df["range_pct"] = (df["High"] - df["Low"]) / df["Close"] * 100
    df["log_value"] = np.log10(df["Value"].clip(lower=1))
    df["log_trades"] = np.log10(df["TradeCount"].clip(lower=1))
    df["locked"] = (df["High"] == df["Low"])
    g = df.groupby("symbol", group_keys=False)
    df["value_ratio_20"] = g["Value"].transform(lambda s: s / s.rolling(20, min_periods=5).mean())
    df["trades_ratio_20"] = g["TradeCount"].transform(lambda s: s / s.rolling(20, min_periods=5).mean())
    df["atv_ratio_20"] = g["avg_trade_value"].transform(lambda s: s / s.rolling(20, min_periods=5).mean())
    df["close_pos_ma5"] = g["close_pos"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    df["up_days_5"] = g["ChangePct"].transform(
        lambda s: (s > 0).rolling(5, min_periods=3).sum())
    return df


def load_daily() -> pd.DataFrame:
    frames = []
    for f in (ROOT / "data" / "daily").glob("*_daily.csv"):
        sym = f.name.replace("_daily.csv", "")
        try:
            d = pd.read_csv(f)
        except Exception:
            continue
        if d.empty:
            continue
        d["symbol"] = sym
        frames.append(d[["symbol", "date", "Open", "High", "Low", "Close"]])
    p = pd.concat(frames, ignore_index=True).sort_values(["symbol", "date"])
    g = p.groupby("symbol", group_keys=False)
    shifted = p.groupby("symbol")[["Open", "High", "Low", "Close"]].shift(-1)
    p["next_open"] = shifted["Open"]
    p["next_locked"] = (shifted["High"] == shifted["Low"]) & (shifted["Close"] > p["Close"])
    p["fwd_high"] = g["High"].transform(lambda s: s.shift(-1).rolling(HOLD, min_periods=1).max())
    p["fwd_low"] = g["Low"].transform(lambda s: s.shift(-1).rolling(HOLD, min_periods=1).min())
    return p[["symbol", "date", "next_open", "next_locked", "fwd_high", "fwd_low"]]


def outcome(df: pd.DataFrame, target_net: float) -> pd.Series:
    entry = df["next_open"].astype(float)
    tp = entry * (BE * (1 + target_net / 100.0))
    sl = entry * (BE * (1 - STOP_NET / 100.0))
    hit = df["fwd_high"] >= tp
    stopped = df["fwd_low"] <= sl
    return hit & ~stopped


FEATURES = [
    "avg_trade_value", "TradeCount", "log_value", "log_trades",
    "vwap", "close_pos", "range_pct", "value_ratio_20", "trades_ratio_20",
    "atv_ratio_20", "close_pos_ma5", "up_days_5",
]


def main() -> None:
    panel = load_trade_panel()
    print(f"trade panel: {len(panel):,} rows, dates={panel['date'].nunique()}")
    feats = build_features(panel)
    daily = load_daily()

    df = feats.merge(daily, on=["symbol", "date"], how="inner")
    df = df[df["next_open"].notna() & df["fwd_high"].notna()]
    df = df[~df["next_locked"].fillna(False)]
    print(f"merged rows: {len(df):,}")

    rows = []
    base = {t: outcome(df, t).mean() * 100 for t in (6, 9, 12)}
    for f in FEATURES:
        s = pd.to_numeric(df[f], errors="coerce")
        if s.notna().sum() < 2000 or s.nunique() < 10:
            continue
        thr = s.quantile(0.75)
        m = s >= thr
        if m.sum() < 500:
            continue
        rec = {"feature": f, "q75": round(float(thr), 2), "n_top": int(m.sum()),
               "n_all": int(s.notna().sum())}
        for t in (6, 9, 12):
            rec[f"win{t}"] = round(outcome(df[m], t).mean() * 100, 1)
        rows.append(rec)

    tbl = pd.DataFrame(rows).sort_values("win6", ascending=False)
    tbl.insert(3, "base6", round(base[6], 1))
    tbl.insert(4, "base9", round(base[9], 1))
    tbl.insert(5, "base12", round(base[12], 1))
    out = ROOT / "reports" / "flow_screen.csv"
    out.parent.mkdir(exist_ok=True)
    tbl.to_csv(out, index=False, encoding="utf-8")
    print(tbl.to_string(index=False))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
