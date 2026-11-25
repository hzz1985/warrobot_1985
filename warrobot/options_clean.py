"""Cleaning engine for the Tehran options dataset.

Input : data/options/*.csv       (raw TSETMC rows, one per contract)
Output: data/options_clean/*.csv (clean per contract)
        data/options_clean/quality.json  (what was dropped and why)

Rules (all configurable at the top):
  * close must be >= MIN_PRICE rial (sub-100 rial prints are noise)
  * a row with volume == 0 is kept only if the close actually changed
    (otherwise it is a stale repeat, not a trade)
  * daily move beyond MAX_DAILY_MOVE is flagged and winsorized
  * contract must keep at least MIN_SESSIONS sessions after cleaning
The raw files are never modified.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
RAW = BASE / "data" / "options"
CLEAN = BASE / "data" / "options_clean"
QUALITY = CLEAN / "quality.json"

MIN_PRICE = 100.0          # rial
MAX_DAILY_MOVE = 0.60      # winsorize above +/-60% in one session
MIN_SESSIONS = 12
MIN_VALUE = 10_000_000     # rial turnover on the entry day (liquidity floor)


def _read(fp: Path) -> list[dict]:
    rows = []
    try:
        with fp.open(encoding="utf-8") as fh:
            head = fh.readline().strip().split(",")
            for line in fh:
                parts = line.rstrip("\n").split(",")
                if len(parts) < len(head):
                    continue
                d = dict(zip(head, parts))
                try:
                    rows.append({
                        "date": str(d["date"]),
                        "open": float(d.get("open") or 0),
                        "high": float(d.get("high") or 0),
                        "low": float(d.get("low") or 0),
                        "close": float(d.get("close") or 0),
                        "volume": float(d.get("volume") or 0),
                        "value": float(d.get("value") or 0),
                        "trades": float(d.get("trades") or 0),
                    })
                except Exception:
                    continue
    except Exception:
        return []
    rows.sort(key=lambda r: r["date"])
    return rows


def clean_contract(rows: list[dict]) -> tuple[list[dict], dict]:
    stats = {"raw": len(rows), "dropped_price": 0, "dropped_stale": 0,
             "winsorized": 0, "kept": 0}
    out = []
    prev = None
    for r in rows:
        if r["close"] < MIN_PRICE:
            stats["dropped_price"] += 1
            continue
        # stale: no volume and identical close to the previous kept row
        if r["volume"] <= 0 and prev is not None and r["close"] == prev["close"]:
            stats["dropped_stale"] += 1
            continue
        if prev is not None and prev["close"] > 0:
            move = r["close"] / prev["close"] - 1
            if abs(move) > MAX_DAILY_MOVE:
                # winsorize the close so the synthetic move never exceeds the cap
                capped = prev["close"] * (1 + (MAX_DAILY_MOVE if move > 0 else -MAX_DAILY_MOVE))
                r = dict(r)
                r["close"] = round(capped, 2)
                stats["winsorized"] += 1
        if prev is not None and r["high"] < r["low"]:
            r = dict(r)
            r["high"], r["low"] = max(r["high"], r["low"]), min(r["high"], r["low"])
        out.append(r)
        prev = r
    stats["kept"] = len(out)
    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-sessions", type=int, default=MIN_SESSIONS)
    args = ap.parse_args()

    CLEAN.mkdir(parents=True, exist_ok=True)
    per_contract = {}
    totals = {"files": 0, "kept_files": 0, "raw_rows": 0, "kept_rows": 0,
              "dropped_price": 0, "dropped_stale": 0, "winsorized": 0}
    prices = []

    for fp in sorted(RAW.glob("*.csv")):
        totals["files"] += 1
        rows = _read(fp)
        totals["raw_rows"] += len(rows)
        if not rows:
            continue
        cleaned, st = clean_contract(rows)
        totals["dropped_price"] += st["dropped_price"]
        totals["dropped_stale"] += st["dropped_stale"]
        totals["winsorized"] += st["winsorized"]
        if len(cleaned) < args.min_sessions:
            per_contract[fp.stem] = {**st, "status": "too_short"}
            continue
        prices.extend(r["close"] for r in cleaned)
        with (CLEAN / fp.name).open("w", encoding="utf-8") as fh:
            fh.write("date,open,high,low,close,volume,value,trades\n")
            for r in cleaned:
                fh.write(",".join(str(x) for x in (
                    r["date"], r["open"], r["high"], r["low"], r["close"],
                    int(r["volume"]), int(r["value"]), int(r["trades"]))) + "\n")
        per_contract[fp.stem] = {**st, "status": "ok"}
        totals["kept_files"] += 1
        totals["kept_rows"] += len(cleaned)

    quality = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "totals": totals,
        "price_median": round(statistics.median(prices), 1) if prices else None,
        "price_p10": round(sorted(prices)[int(0.1 * len(prices))], 1) if prices else None,
        "settings": {"min_price": MIN_PRICE, "max_daily_move": MAX_DAILY_MOVE,
                     "min_sessions": args.min_sessions, "min_value": MIN_VALUE},
        "contracts": per_contract,
    }
    QUALITY.write_text(json.dumps(quality, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"files": totals["files"], "kept_files": totals["kept_files"],
                      "raw_rows": totals["raw_rows"], "kept_rows": totals["kept_rows"],
                      "dropped_price": totals["dropped_price"],
                      "dropped_stale": totals["dropped_stale"],
                      "winsorized": totals["winsorized"],
                      "price_median": quality["price_median"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
