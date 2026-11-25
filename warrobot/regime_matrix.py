"""Regime matrix: market state x stock setup -> probability of hitting the target.

Builds, for every locked session in 1405, a feature vector:

  market (regime)   : breadth (share of symbols up), market turnover vs its 20d
                      median, average close position, dispersion of returns
  stock (setup)     : distance to ceiling, close position, RSI(14), Bollinger,
                      20-day return, volume vs its 20d median
  target            : did the trade (+TP/-SL, 7 sessions) reach TP?

Solved with a logistic regression on a time split (first 70% train, last 30% test)
so the reported AUC is out of sample. Regime buckets are also reported separately.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
DAILY = BASE / "TeseRobot" / "data" / "daily"
D3Y = BASE / "Tse2" / "orderflow" / "data" / "daily3y"
OUT = BASE / "WarRobot" / "state" / "regime_matrix.json"
CSV = BASE / "WarRobot" / "state" / "regime_matrix.csv"

TP, SL, HOLD = 9.0, 4.0, 7


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).rolling(n, min_periods=5).mean()
    dn = (-d.clip(upper=0)).rolling(n, min_periods=5).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def load(sym: str):
    for p in (DAILY / f"{sym}_daily.csv", D3Y / f"{sym}.csv"):
        if p.exists():
            try:
                return pd.read_csv(p, dtype={"date": str}).sort_values("date").reset_index(drop=True)
            except Exception:
                return None
    return None


def main() -> int:
    symbols = sorted({f.name[:-len("_daily.csv")] for f in DAILY.glob("*_daily.csv")} |
                     {f.stem for f in D3Y.glob("*.csv")})
    data, rets, turns = {}, [], []
    for sym in symbols:
        d = load(sym)
        if d is None or len(d) < 60:
            continue
        d = d[d["date"] >= "1405-01-01"].reset_index(drop=True)
        if len(d) < 25:
            continue
        d["ret"] = d["Close"].astype(float).pct_change()
        d["turn"] = d["Volume"].astype(float) * d["Close"].astype(float)
        data[sym] = d
    if not data:
        print("no data")
        return 1

    # ---- market regime per session
    allret = pd.concat([d.set_index("date")["ret"].rename(s) for s, d in data.items()], axis=1)
    allturn = pd.concat([d.set_index("date")["turn"].rename(s) for s, d in data.items()], axis=1)
    regime = pd.DataFrame({
        "breadth": (allret > 0).mean(axis=1) * 100,
        "mkt_ret": allret.mean(axis=1) * 100,
        "dispersion": allret.std(axis=1) * 100,
        "turnover": allturn.sum(axis=1) / 1e12,
    })
    regime["turn_ratio"] = regime["turnover"] / regime["turnover"].shift(1).rolling(20, min_periods=10).median()
    regime["mkt_ma10"] = regime["mkt_ret"].rolling(10, min_periods=5).mean()
    regime["trend"] = np.sign(regime["mkt_ma10"]).fillna(0)
    print("regime rows:", len(regime))

    rows = []
    for sym, d in data.items():
        close = d["Close"].astype(float)
        prev = close.shift(1)
        ceil = (prev * 1.03).round(0)
        locked = (d["High"] == d["Close"]) & (d["High"] >= prev * 1.029)
        r14 = rsi(close)
        ma20 = close.rolling(20, min_periods=10).mean()
        sd = close.rolling(20, min_periods=10).std()
        bb = (close - (ma20 - 2 * sd)) / ((4 * sd).replace(0, np.nan))
        rng = (d["High"].astype(float) - d["Low"].astype(float))
        cpos = ((close - d["Low"].astype(float)) / rng.replace(0, np.nan)).where(rng > 0, 0.5)
        volr = d["turn"] / d["turn"].shift(1).rolling(20, min_periods=10).median()
        r20 = close.pct_change(20) * 100

        for i in range(21, len(d) - 1):
            if not locked.iloc[i]:
                continue
            j0 = i + 1
            entry = float(d["Open"].iloc[j0])
            if entry <= 0:
                continue
            hit = 0
            for k in range(HOLD):
                j = j0 + k
                if j >= len(d):
                    break
                if float(d["High"].iloc[j]) >= entry * (1 + TP / 100):
                    hit = 1
                    break
                if float(d["Low"].iloc[j]) <= entry * (1 - SL / 100):
                    hit = 0
                    break
            date = d["date"].iloc[i]
            if date not in regime.index:
                continue
            rg = regime.loc[date]
            rows.append({
                "date": date, "symbol": sym, "hit": hit,
                "dist": float((ceil.iloc[i] - close.iloc[i]) / close.iloc[i] * 100),
                "cpos": float(cpos.iloc[i]) if not pd.isna(cpos.iloc[i]) else 0.5,
                "rsi": float(r14.iloc[i]) if not pd.isna(r14.iloc[i]) else 50,
                "bb": float(bb.iloc[i]) if not pd.isna(bb.iloc[i]) else 0.5,
                "volr": float(volr.iloc[i]) if not pd.isna(volr.iloc[i]) else 1.0,
                "r20": float(r20.iloc[i]) if not pd.isna(r20.iloc[i]) else 0.0,
                "breadth": float(rg["breadth"]), "mkt_ret": float(rg["mkt_ret"]),
                "dispersion": float(rg["dispersion"]), "turn_ratio": float(rg["turn_ratio"]) if not pd.isna(rg["turn_ratio"]) else 1.0,
                "trend": float(rg["trend"]),
            })
    m = pd.DataFrame(rows).dropna()
    m.to_csv(CSV, index=False, encoding="utf-8-sig")
    print("samples:", len(m), "| base hit rate:", round(m.hit.mean() * 100, 1), "%")

    # ---- regime buckets
    m["risk"] = pd.cut(m.breadth, [-1, 35, 55, 101], labels=["ریسک‌گریز", "خنثی", "ریسک‌پذیر"])
    g = m.groupby("risk", observed=True).hit.agg(["count", "mean"])
    print("\nنرخ رسیدن به هدف بر اساس رژیم:")
    for k, r in g.iterrows():
        print(f"  {k}: n={int(r['count'])} · نرخ برد {r['mean']*100:.1f}%")

    # ---- logistic regression, time split
    feats = ["dist", "cpos", "rsi", "bb", "volr", "r20", "breadth", "mkt_ret", "dispersion", "turn_ratio", "trend"]
    m = m.sort_values("date")
    cut = int(len(m) * 0.7)
    tr, te = m.iloc[:cut], m.iloc[cut:]

    def fit(df):
        X = df[feats].to_numpy(float)
        X = (X - X.mean(0)) / (X.std(0) + 1e-9)
        y = df["hit"].to_numpy(float)
        Xb = np.c_[np.ones(len(X)), X]
        w = np.zeros(Xb.shape[1])
        for _ in range(400):
            z = Xb @ w
            p = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
            grad = Xb.T @ (p - y) / len(y)
            w -= 0.5 * grad
        return w, X.mean(0), X.std(0) + 1e-9

    def predict(w, mu, sd, df):
        X = (df[feats].to_numpy(float) - mu) / sd
        z = np.c_[np.ones(len(X)), X] @ w
        return 1 / (1 + np.exp(-np.clip(z, -30, 30)))

    w, mu, sd = fit(tr)
    p_te = predict(w, mu, sd, te)

    def auc(y, p):
        pos, neg = p[y == 1], p[y == 0]
        if len(pos) == 0 or len(neg) == 0:
            return float("nan")
        return float((pos[:, None] > neg[None, :]).mean())

    auc_te = auc(te["hit"].to_numpy(), p_te)
    print(f"\nآزمون خارج از نمونه: n={len(te)} · AUC={auc_te:.3f} · نرخ برد تست {te.hit.mean()*100:.1f}%")

    # top decile by predicted probability
    te = te.copy()
    te["p"] = p_te
    top = te.nlargest(max(10, len(te) // 10), "p")
    print(f"دهک بالا بر اساس مدل: نرخ برد {top.hit.mean()*100:.1f}% (n={len(top)})")

    coef = sorted(zip(feats, w[1:]), key=lambda x: -abs(x[1]))
    print("\nمهم‌ترین متغیرها:")
    for k, v in coef[:7]:
        print(f"  {k:12s} {v:+.3f}")

    OUT.write_text(json.dumps({
        "samples": int(len(m)), "base_hit": round(float(m.hit.mean()) * 100, 1),
        "auc_test": round(auc_te, 3), "top_decile_hit": round(float(top.hit.mean()) * 100, 1),
        "regime": {str(k): {"n": int(r["count"]), "hit": round(float(r["mean"]) * 100, 1)}
                   for k, r in g.iterrows()},
        "coef": [{"feature": k, "w": round(float(v), 3)} for k, v in coef],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
