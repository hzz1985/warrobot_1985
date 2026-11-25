"""Conditional analysis: does the price/volume signal precede Type-A disclosures?

Data now available (all real):
  * state/codal_typeA_events.csv  -> 2,141 Type-A disclosures (1400..1405) from codal
  * TeseRobot/data/daily/*.csv    -> 790 symbols, 1404-01-05 .. 1405-08-20

Steps
  1. parse the Jalali publish dates (Persian digits) and map each event to the
     symbol's daily row (the first session on/after publication)
  2. measure the pre-event window (T-20..T-1): volume ratio, proximity to the
     60-session high, 20-session return
  3. label the event: positive if the price rose >= 3% within 3 sessions
  4. conditional test: for EVERY signal day in the dataset, was a Type-A event
     published within the next 10 sessions? compare with the base rate
  5. compare returns of signals that preceded an event vs signals that did not
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
EVENTS = (Path(sys.argv[1]) if len(sys.argv) > 1
          else BASE / "state" / "codal_typeA_events.csv")
DAILY = BASE.parent / "TeseRobot" / "data" / "daily"
OUT = (Path(sys.argv[2]) if len(sys.argv) > 2
       else BASE / "research" / "typeA_conditional_result.json")

PERSIAN = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
VOL_RATIO = 1.8
HIGH_PROX = 0.90
HORIZON = 10          # event must arrive within this many sessions
LABEL_UP = 0.03


def jalali_str(published: str) -> str:
    """"۱۴۰۴/۰۳/۳۰ ۱۵:۵۸:۲۲" -> "1404-03-30"."""
    txt = (published or "").translate(PERSIAN)
    parts = txt.split(" ")[0].split("/")
    if len(parts) != 3:
        return ""
    y, m, d = parts
    if len(d) == 1:
        d = "0" + d
    if len(m) == 1:
        m = "0" + m
    return f"{y}-{m}-{d}"


def load_events() -> dict:
    df = pd.read_csv(EVENTS, encoding="utf-8-sig")
    out = defaultdict(list)
    for _, r in df.iterrows():
        sym = str(r.get("symbol") or "").strip()
        d = jalali_str(str(r.get("published") or ""))
        if sym and d:
            out[sym].append({"date": d, "title": str(r.get("title") or "")})
    for k in out:
        out[k].sort(key=lambda x: x["date"])
    return out


def main() -> int:
    events = load_events()
    print(f"type-A symbols: {len(events)} · events: {sum(len(v) for v in events.values())}")

    files = {p.stem.replace("_daily", ""): p for p in DAILY.glob("*_daily.csv")}
    matched_events = 0
    pre_features = []
    labels = {"positive": 0, "negative": 0, "neutral": 0}

    # per-symbol daily frames (only symbols that have events and a data file)
    frames = {}
    for sym, evs in events.items():
        fp = files.get(sym)
        if not fp:
            continue
        try:
            d = pd.read_csv(fp).dropna(subset=["Close", "Volume"])
        except Exception:
            continue
        if len(d) < 90:
            continue
        d = d[d["Close"] > 0].reset_index(drop=True)
        d["vol_ma20"] = d["Volume"].rolling(20).mean()
        d["high60"] = d["High"].rolling(60).max()
        d["vol_ratio"] = d["Volume"] / d["vol_ma20"]
        d["prox_high"] = d["Close"] / d["high60"]
        d["ret20"] = d["Close"] / d["Close"].shift(20) - 1
        frames[sym] = d

    for sym, evs in events.items():
        d = frames.get(sym)
        if d is None:
            continue
        dates = list(d["date"])
        for e in evs:
            # first session on/after the publication
            idx = None
            for i, dt in enumerate(dates):
                if dt >= e["date"]:
                    idx = i
                    break
            if idx is None or idx < 60:
                continue
            matched_events += 1
            t = d.iloc[idx - 1]           # T-1 (last session before the event)
            pre_features.append({"symbol": sym, "date": e["date"],
                                 "vol_ratio": float(t["vol_ratio"]) if pd.notna(t["vol_ratio"]) else None,
                                 "prox_high": float(t["prox_high"]) if pd.notna(t["prox_high"]) else None,
                                 "ret20": float(t["ret20"]) if pd.notna(t["ret20"]) else None,
                                 "volume_ratio_5d": float((d.iloc[idx - 5:idx]["Volume"].mean()) /
                                                          max(1e-9, d.iloc[idx - 25:idx - 5]["Volume"].mean()))})
            fwd = d.iloc[idx: idx + 3]["Close"]
            if len(fwd) >= 2:
                chg = fwd.iloc[-1] / fwd.iloc[0] - 1
                labels["positive" if chg >= LABEL_UP else
                       "negative" if chg <= -LABEL_UP else "neutral"] += 1

    print(f"matched events with local price data: {matched_events}")
    print("labels:", labels)

    # ---------- pre-event feature summary ----------
    def summ(key):
        vals = [f[key] for f in pre_features if f.get(key) is not None]
        if not vals:
            return None
        vals_sorted = sorted(vals)
        return {"n": len(vals), "mean": round(statistics.fmean(vals), 3),
                "median": round(statistics.median(vals), 3),
                "p90": round(vals_sorted[int(0.9 * len(vals))], 3),
                "min": round(vals_sorted[0], 3), "max": round(vals_sorted[-1], 3)}

    pre_summary = {k: summ(k) for k in ("vol_ratio", "prox_high", "ret20", "volume_ratio_5d")}
    print("pre-event features:", json.dumps(pre_summary, ensure_ascii=False))

    # signal rate on the day before an event (does the rule fire?)
    fired = [f for f in pre_features
             if (f.get("vol_ratio") or 0) > VOL_RATIO and (f.get("prox_high") or 0) >= HIGH_PROX]
    signal_before_event = round(100 * len(fired) / max(1, len(pre_features)), 2)
    print(f"signal fired on T-1 before an event: {signal_before_event}%")

    # ---------- conditional test over all symbol-days ----------
    event_dates = {sym: [e["date"] for e in evs] for sym, evs in events.items()}
    total_signal_days = 0
    signal_hit = 0
    total_days = 0
    base_hits = 0
    ret_hit, ret_miss = [], []
    for sym, d in frames.items():
        dates = list(d["date"])
        evs = event_dates.get(sym, [])
        if not evs:
            continue
        ev_set = set(evs)
        for i in range(60, len(d) - 1):
            row = d.iloc[i]
            if pd.isna(row["vol_ratio"]) or pd.isna(row["prox_high"]):
                continue
            total_days += 1
            # was a Type-A event published within the next HORIZON sessions?
            window = dates[i:i + HORIZON + 1]
            has_event = any(w in ev_set for w in window)
            if has_event:
                base_hits += 1
            if row["vol_ratio"] > VOL_RATIO and row["prox_high"] >= HIGH_PROX:
                total_signal_days += 1
                if has_event:
                    signal_hit += 1
                entry = float(d.iloc[i + 1]["Open"]) or float(d.iloc[i + 1]["Close"])
                ret3 = float(d.iloc[min(i + 4, len(d) - 1)]["Close"]) / entry - 1 if entry else 0.0
                (ret_hit if has_event else ret_miss).append(ret3)

    base_rate = round(100 * base_hits / max(1, total_days), 2)
    hit_rate = round(100 * signal_hit / max(1, total_signal_days), 2)
    result = {
        "events_total": int(sum(len(v) for v in events.values())),
        "events_with_price": matched_events,
        "labels": labels,
        "pre_event_features": pre_summary,
        "signal_fired_before_event_pct": signal_before_event,
        "conditional_test": {
            "symbol_days": total_days,
            "base_rate_pct": base_rate,
            "signal_days": total_signal_days,
            "hit_rate_pct": hit_rate,
            "lift": round(hit_rate / base_rate, 2) if base_rate else None,
            "avg_ret3_after_signal_with_event_pct": round(100 * statistics.fmean(ret_hit), 3) if ret_hit else None,
            "avg_ret3_after_signal_no_event_pct": round(100 * statistics.fmean(ret_miss), 3) if ret_miss else None,
        },
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result["conditional_test"], ensure_ascii=False, indent=1))
    print("saved:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
