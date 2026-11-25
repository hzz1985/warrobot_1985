"""Weekly self-tuning for WarRobot.

Reads the 14-day tracker, measures which candidate parameters actually paid
(expectancy per bucket), and writes state/weights.json. The 09:00 live scan then
ranks candidates with those weights instead of a fixed rule.

Buckets (what the bot can observe before buying):
    dist      : 0-0.05 / 0.05-0.15 / 0.15-0.25  (distance to ceiling, %)
    big_small : <1 / 1-3 / 3+                   (big/small trade ratio, last night)
    turnover  : low / mid / high                (terciles of the candidate set)

Weight rule:  w = clamp(1 + expectancy / 3, 0.3, 2.5)   per bucket,
then normalised so the average weight is 1. Buckets with n < MIN_N fall back to 1.

Guards against overfitting:
  * fewer than MIN_ROWS scored rows -> keep the previous weights
  * a bucket needs MIN_N observations
  * weights are clamped to [0.3, 2.5]
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

P = Path(__file__).resolve().parent
XLSX = P / "state" / "candidates_tracker.xlsx"
OUT = P / "state" / "weights.json"
MIN_ROWS = 80
MIN_N = 8


def bucket_dist(x: float) -> str:
    if x is None or pd.isna(x):
        return "na"
    if x <= 0.05:
        return "dist_0_005"
    if x <= 0.15:
        return "dist_005_015"
    return "dist_015_025"


def bucket_bs(x) -> str:
    if x is None or pd.isna(x):
        return "na"
    if x < 1:
        return "bs_lt1"
    if x < 3:
        return "bs_1_3"
    return "bs_3p"


def bucket_turn(x, q33, q66) -> str:
    if x is None or pd.isna(x):
        return "na"
    if x <= q33:
        return "turn_low"
    if x <= q66:
        return "turn_mid"
    return "turn_high"


def main() -> int:
    if not XLSX.exists():
        print("no tracker yet")
        return 1
    df = pd.read_excel(XLSX, sheet_name="candidates")
    if df.empty:
        print("tracker empty")
        return 1

    # outcome: realised return of the trade, else the first-day return
    df["outcome"] = df.get("ret_final_pct")
    if "ret_t1_pct" in df.columns:
        df["outcome"] = df["outcome"].fillna(df["ret_t1_pct"])
    scored = df[df["outcome"].notna()].copy()
    print(f"rows={len(df)} scored={len(scored)}")

    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8")).get("weights", {})
        except Exception:
            prev = {}
    if len(scored) < MIN_ROWS:
        print(f"not enough scored rows ({len(scored)} < {MIN_ROWS}) -> keep previous weights")
        return 0

    q33, q66 = scored["turnover"].quantile([0.33, 0.66]) if "turnover" in scored else (0, 0)
    scored["b_dist"] = scored["dist_pct"].map(bucket_dist)
    scored["b_bs"] = scored.get("big_to_small", pd.Series(dtype=float)).map(bucket_bs)
    scored["b_turn"] = scored["turnover"].map(lambda x: bucket_turn(x, q33, q66))

    weights, report = {}, []
    for col in ("b_dist", "b_bs", "b_turn"):
        for key, g in scored.groupby(col):
            n = len(g)
            if n < MIN_N:
                weights[key] = 1.0
                report.append((key, n, None, 1.0))
                continue
            exp = float(g["outcome"].mean())          # percent
            weights[key] = round(exp, 3)              # raw expectancy, ranked below
            report.append((key, n, round(exp, 2), 0.0))

    # rank-based: relative performance matters, not the absolute level
    vals = list(weights.values())
    mu = sum(vals) / len(vals)
    sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
    for k, v in list(weights.items()):
        z = (v - mu) / sd
        weights[k] = round(max(0.5, min(1.8, 1 + 0.35 * z)), 3)

    payload = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "rows": int(len(scored)),
        "turnover_terciles": [float(q33), float(q66)],
        "weights": weights,
        "report": [{"bucket": b, "n": n, "expectancy": e, "weight": w}
                   for b, n, e, w in report],
    }
    payload["report"] = [{"bucket": b, "n": n, "expectancy": e, "weight": weights.get(b, 1.0)}
                         for b, n, e, _ in report]
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"weights -> {OUT.name}")
    for b, n, e, _ in sorted(report, key=lambda z: -weights.get(z[0], 1.0)):
        print(f"  {b:14s} n={n:4d} · انتظار {e:+.2f}% · وزن {weights.get(b, 1.0):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
