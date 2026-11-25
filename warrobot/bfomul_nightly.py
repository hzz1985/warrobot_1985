"""Nightly BFomul scan — replaces the band scan as the buy source.

For every symbol that HAS a BFomul (from bfomul_by_symbol.xlsx) it evaluates the
four conditions on the latest session and writes:

  * state/candidates.csv      -> what the bot buys (with the reason column)
  * Desktop/BFormulExcel.xlsx -> every signal, appended daily, 14-day follow-up
  * an email summary

Symbols without a BFomul are skipped entirely (per the agreed policy).
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
PROJ = BASE / "WarRobot"
CAL = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\data\bfomul_by_symbol.xlsx")
XLSX = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\data\BFormulExcel.xlsx")
TAPE = BASE / "Tse2" / "orderflow" / "tape_1405.db"
CAND = PROJ / "state" / "candidates.csv"
DAILY = BASE / "TeseRobot" / "data" / "daily"
D3Y = BASE / "Tse2" / "orderflow" / "data" / "daily3y"

sys.path.insert(0, str(BASE / "TeseRobot" / "src"))


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


def _ledger_positions():
    """Symbols currently held, read straight from the ledger jsonl."""
    import json as _j
    fp = PROJ / "state" / "ledger.jsonl"
    rows = []
    if fp.exists():
        for line in fp.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(_j.loads(line))
            except Exception:
                pass
    buys, sells = {}, {}
    for r in rows:
        s_ = r.get("symbol")
        if not s_:
            continue
        q = float(r.get("qty") or 0)
        if r.get("side") == "buy":
            buys[s_] = buys.get(s_, 0) + q
        elif r.get("side") == "sell":
            sells[s_] = sells.get(s_, 0) + q
    return [s_ for s_ in buys if buys[s_] - sells.get(s_, 0) > 0]


def main() -> int:
    if not CAL.exists():
        print("no calibration file")
        return 1
    live = {}
    live_fp = PROJ / "state" / "bfomul_live.json"
    if live_fp.exists():
        live = json.loads(live_fp.read_text(encoding="utf-8"))
    cal = pd.read_excel(CAL)
    if live:
        rows_live = [{"symbol": k, **v} for k, v in live.items()]
        cal = pd.DataFrame(rows_live)
    # ranking data: how trustworthy is each symbol's formula?
    meta = {}
    for _, r in cal.iterrows():
        meta[str(r["symbol"])] = {
            "precision": float(r.get("hit_rate") or r.get("precision") or 0),
            "signals": int(r.get("signals") or 0),
            "tp": float(r.get("tp") or 15.0),
            "per_day": float(r.get("return_per_day") or 0.0),
            "sl": float(r.get("sl") or 4.0),
            "hold": int(r.get("hold") or 15),
        }
    MIN_PREC = 70.0
    MIN_SIG = 5
    MAX_PICK = 3
    con = sqlite3.connect(TAPE)
    sys.path.insert(0, str(BASE / "Tse2" / "orderflow"))
    import tsetmc as api

    signals, rows = [], []
    for _, r in cal.iterrows():
        sym = str(r["symbol"])
        d = load(sym)
        if d is None or len(d) < 30:
            continue
        i = len(d) - 1
        close = d["Close"].astype(float)
        r14 = rsi(close)
        ma20 = close.rolling(20, min_periods=10).mean()
        sd20 = close.rolling(20, min_periods=10).std()
        bb = (close - (ma20 - 2 * sd20)) / ((4 * sd20).replace(0, np.nan))
        if pd.isna(r14.iloc[i]) or pd.isna(bb.iloc[i]):
            continue

        code = None
        try:
            m = api.resolve(sym)
            code = m["ins_code"] if m else None
        except Exception:
            code = None
        ceil_b = share = None
        if code:
            try:
                y, mo, dd = (int(x) for x in str(d["date"].iloc[i]).split("-"))
                de = int(jdatetime.date(y, mo, dd).togregorian().strftime("%Y%m%d"))
                q = con.execute("SELECT cel, tot FROM day_ceiling WHERE ins_code=? AND d_even=?",
                                (code, de)).fetchone()
                if q and q[1]:
                    ceil_b = round((q[0] or 0) / 1e9, 2)
                    share = round((q[0] or 0) / q[1] * 100, 1)
            except Exception:
                pass
        if ceil_b is None:          # no tape for this session yet -> daily proxy
            locked = bool(d["High"].iloc[i] == d["Close"].iloc[i] and
                          d["High"].iloc[i] >= close.iloc[i - 1] * 1.029)
            ceil_b = round(float(d["Volume"].iloc[i] * close.iloc[i]) / 1e9, 2)
            share = 100.0 if locked else 0.0

        ceil_max = 1e9 if pd.isna(r.get("ceil_max_b")) else float(r["ceil_max_b"])
        c = {
            "RSI": bool(r["rsi_min"] <= r14.iloc[i] <= r["rsi_max"]),
            "BB": bool(bb.iloc[i] < r["bb_max"]),
            "سقف": bool(ceil_b < ceil_max),
            "سهم سقف": bool(share >= r["share_min"]),
        }
        hit = all(c.values())
        reason = "؛ ".join(f"{k}={'✔' if v else '✘'}" for k, v in c.items())
        rows.append({"تاریخ": d["date"].iloc[i], "نماد": sym, "سیگنال": int(hit),
                     "RSI": round(float(r14.iloc[i]), 1), "BB": round(float(bb.iloc[i]), 2),
                     "ارزش سقف (میلیارد)": ceil_b, "سهم سقف %": share,
                     "علت": reason, "آستانه‌ها": f"RSI {r['rsi_min']}-{r['rsi_max']} · "
                     f"BB<{r['bb_max']} · سقف<{ceil_max if ceil_max < 1e9 else '∞'} · "
                     f"سهم≥{r['share_min']}%"})
        if hit:
            m = meta.get(sym, {"precision": 0, "long_episodes": 0, "passes": False})
            ranked = (m["precision"] >= MIN_PREC and m["signals"] >= MIN_SIG)
            nxt = float(round(close.iloc[i] * 1.03))
            score = round(m["precision"] * max(m["signals"], 1) * max(m.get("per_day", 0.0), 0.01), 1)
            signals.append({"symbol": sym, "rank_score": score,
                            "symbol_precision": m["precision"], "symbol_signals": m["signals"],
                            "tp_pct": m["tp"], "sl_pct": m["sl"], "hold_days": m["hold"],
                            "ranked": int(ranked), "close": float(close.iloc[i]),
                            "ceiling": float(round(close.iloc[i - 1] * 1.03)) if i else nxt,
                            "next_ceiling": nxt, "dist_pct": 0.0, "volume": float(d["Volume"].iloc[i]),
                            "turnover": float(d["Volume"].iloc[i] * close.iloc[i]),
                            "session": str(d["date"].iloc[i]),
                            "reason": f"BFomul: {reason}"})
    con.close()

    # AUTO_SELL_RULE: a held position that is NOT a candidate tonight is queued
    # for sale; one that IS a candidate stays.
    try:
        led = _ledger_positions()
        held = [x for x in led]
        picked_syms = {str(x["symbol"]) for x in signals if x.get("ranked")}
        data = {"symbols": [], "note": "خودکار: پوزیشن‌های بدون سیگنال برای فروش"}
        for h in held:
            if h not in picked_syms:
                data["symbols"].append(h)
        (PROJ / "state" / "force_sell.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print("force_sell ->", data["symbols"])
    except Exception as exc:
        print("auto-sell rule error:", str(exc)[:80])

    # keep only trustworthy symbols, then the best three
    trusted = [x for x in signals if x.get("ranked")]
    trusted.sort(key=lambda z: -z["rank_score"])
    picked = trusted[:MAX_PICK]
    for j, x in enumerate(picked, 1):
        x["pick_rank"] = j
    if picked:
        pd.DataFrame(picked).to_csv(CAND, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=["symbol", "close", "ceiling", "next_ceiling", "dist_pct",
                              "volume", "turnover", "session", "reason"]).to_csv(CAND, index=False,
                                                                                  encoding="utf-8-sig")

    # growing signal log
    new = pd.DataFrame(rows)
    if XLSX.exists():
        try:
            old = pd.read_excel(XLSX, sheet_name="signals")
            new = pd.concat([old, new], ignore_index=True).drop_duplicates(subset=["تاریخ", "نماد"], keep="last")
        except Exception:
            pass
    new.to_excel(XLSX, sheet_name="signals", index=False)

    print(f"evaluated={len(rows)} signals={len(signals)} -> {CAND.name} · {XLSX.name}")
    try:
        from tsebot.alerts import send
        body = [f"اسکن شبانهٔ BFomul — {datetime.now():%Y-%m-%d %H:%M}",
                f"نمادهای بررسی‌شده: {len(rows)} · سیگنال: {len(signals)}", ""]
        for s in signals[:15]:
            body.append(f"  {s['symbol']}: {s['reason']}")
        if not signals:
            body.append("هیچ نمادی سیگنال نداد.")
        send("[WarRobot] اسکن شبانه BFomul", "\n".join(body), key="bfomul-nightly", force=True)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
