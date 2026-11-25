"""Quarterly BFomul recalibration (rolling 12 months).

Policy agreed with the operator:
  * window      : the last 12 months (rolling)
  * accept rule : the new thresholds are adopted ONLY if their precision is >= 75%
                  on that window, otherwise the previous formula is kept
  * thin symbols: fewer than 3 episodes of >=5 locked days -> keep the previous formula

Writes bfomul_by_symbol.xlsx in place plus a `changelog` sheet and emails a summary.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import jdatetime
import numpy as np
import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
TAPE = BASE / "Tse2" / "orderflow" / "tape_1405.db"
XLSX = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\data\bfomul_by_symbol.xlsx")
LOG = BASE / "WarRobot" / "state" / "bfomul_quarterly.json"
DAILY = BASE / "TeseRobot" / "data" / "daily"
D3Y = BASE / "Tse2" / "orderflow" / "data" / "daily3y"
MIN_LEN = 5
MIN_EPISODES = 3
MIN_PRECISION = 75.0

RSI_MINS = [30, 35, 40]
RSI_MAXS = [70, 75, 80]
BB_MAXS = [0.80, 0.85, 0.90]
CEIL_MAXS = [300, 400, 500, 600, 900, 1e9]
SHARE_MINS = [0, 90, 95]


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
    if not XLSX.exists():
        print("no calibration file")
        return 1
    cal = pd.read_excel(XLSX, sheet_name="Sheet1" if "Sheet1" in pd.ExcelFile(XLSX).sheet_names else 0)
    con = sqlite3.connect(TAPE)
    sys.path.insert(0, str(BASE / "Tse2" / "orderflow"))
    import tsetmc as api

    today = jdatetime.date.today()
    cutoff = today - jdatetime.timedelta(days=365)
    cutoff_s = cutoff.strftime("%Y-%m-%d")

    changes, kept, skipped = [], 0, 0
    for idx, r in cal.iterrows():
        sym = str(r["symbol"])
        d = load(sym)
        if d is None or len(d) < 60:
            skipped += 1
            continue
        d = d[d["date"] >= cutoff_s].reset_index(drop=True)
        if len(d) < 40:
            skipped += 1
            continue
        close = d["Close"].astype(float)
        r14 = rsi(close)
        ma20 = close.rolling(20, min_periods=10).mean()
        sd20 = close.rolling(20, min_periods=10).std()
        bb = (close - (ma20 - 2 * sd20)) / ((4 * sd20).replace(0, np.nan))
        prev = close.shift(1)
        locked = (d["High"] == d["Close"]) & (d["High"] >= prev * 1.029)
        runs, cur = [], []
        for i, v in enumerate(locked):
            if v:
                cur.append(i)
            else:
                if cur:
                    runs.append(cur)
                    cur = []
        if cur:
            runs.append(cur)
        long_runs = [x for x in runs if len(x) >= MIN_LEN]
        if len(long_runs) < MIN_EPISODES:
            kept += 1
            continue

        code = None
        try:
            m = api.resolve(sym)
            code = m["ins_code"] if m else None
        except Exception:
            code = None
        rows = []
        for rn in long_runs + [x for x in runs if len(x) < MIN_LEN]:
            i = rn[0]
            if i == 0:
                continue
            try:
                y, mo, dd = (int(v) for v in str(d["date"].iloc[i]).split("-"))
                de = int(jdatetime.date(y, mo, dd).togregorian().strftime("%Y%m%d"))
            except Exception:
                continue
            cel = tot = None
            if code:
                q = con.execute("SELECT cel, tot FROM day_ceiling WHERE ins_code=? AND d_even=?",
                                (code, de)).fetchone()
                if q and q[1]:
                    cel, tot = q[0] or 0, q[1]
            if cel is None:
                cel = float(d["Volume"].iloc[i] * close.iloc[i])
                tot = cel
                share = 100.0 if locked.iloc[i] else 0.0
            else:
                share = cel / tot * 100 if tot else 0.0
            if pd.isna(r14.iloc[i - 1]) or pd.isna(bb.iloc[i - 1]):
                continue
            rows.append({"len": len(rn), "rsi": r14.iloc[i - 1], "bb": bb.iloc[i - 1],
                         "ceil": cel / 1e9, "share": share})
        t = pd.DataFrame(rows)
        if not len(t):
            skipped += 1
            continue
        pos = (t.len >= MIN_LEN).to_numpy()
        best = None
        for rl in RSI_MINS:
            for rh in RSI_MAXS:
                for bm in BB_MAXS:
                    for cm in CEIL_MAXS:
                        for sm in SHARE_MINS:
                            sig = ((t.rsi >= rl) & (t.rsi <= rh) & (t.bb < bm) &
                                   (t.ceil < cm) & (t.share >= sm)).to_numpy()
                            tp = int((sig & pos).sum())
                            fp = int((sig & ~pos).sum())
                            fn = int((~sig & pos).sum())
                            if tp == 0:
                                continue
                            prec = tp / (tp + fp) * 100
                            rec = tp / (tp + fn) * 100
                            cand = (round(prec, 1), round(rec, 1), tp, fp, rl, rh, bm, cm, sm)
                            if best is None or cand > best:
                                best = cand
        if best and best[0] >= MIN_PRECISION:
            prec, rec, tp, fp, rl, rh, bm, cm, sm = best
            old = (int(r["rsi_min"]), int(r["rsi_max"]), float(r["bb_max"]),
                   r.get("ceil_max_b"), int(r["share_min"]))
            new = (rl, rh, bm, (None if cm >= 1e9 else cm), sm)
            cal.at[idx, "rsi_min"] = rl
            cal.at[idx, "rsi_max"] = rh
            cal.at[idx, "bb_max"] = bm
            cal.at[idx, "ceil_max_b"] = None if cm >= 1e9 else cm
            cal.at[idx, "share_min"] = sm
            cal.at[idx, "precision"] = prec
            cal.at[idx, "recall"] = rec
            cal.at[idx, "passes_75"] = True
            if old != new:
                changes.append({"نماد": sym, "قبل": str(old), "بعد": str(new),
                                "دقت": prec, "پوشش": rec})
        else:
            kept += 1

    con.close()
    with pd.ExcelWriter(XLSX, engine="openpyxl") as xw:
        cal.to_excel(xw, sheet_name="Sheet1", index=False)
        pd.DataFrame(changes).to_excel(xw, sheet_name="changelog", index=False)

    summary = {"run": datetime.now().strftime("%Y-%m-%d %H:%M"), "window_from": cutoff_s,
               "changed": len(changes), "kept": kept, "skipped": skipped}
    LOG.write_text(json.dumps({"summary": summary, "changes": changes}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    try:
        from tsebot.alerts import send
        body = [f"بازنویسی سه‌ماههٔ BFomul — {summary['run']}",
                f"پنجره: از {cutoff_s} (۱۲ ماه غلتان)",
                f"تغییر یافته: {len(changes)} · حفظ قبلی: {kept} · رد شده: {skipped}", ""]
        for c in changes[:20]:
            body.append(f"  {c['نماد']}: {c['قبل']} → {c['بعد']} · دقت {c['دقت']}%")
        if not changes:
            body.append("هیچ فرمولی به آستانهٔ ۷۵٪ نرسید؛ همهٔ فرمول‌های قبلی حفظ شدند.")
        send("[WarRobot] بازنویسی سه‌ماهه BFomul", "\n".join(body), key="bfomul-q", force=True)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
