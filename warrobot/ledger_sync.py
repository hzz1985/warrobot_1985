"""Reconcile the bot's ledger with the broker's real portfolio.

The dashboard now shows broker positions, but the bot's own TP/SL logic still
uses the ledger. If they disagree (e.g. آردینه 18,322 in the ledger vs 22,649 at
the broker) the bot would sell the wrong quantity and compute the wrong P/L.

This script aligns the ledger to the broker:
  * broker qty > ledger qty  -> add a reconciliation buy lot
        price = the bot's current average for that symbol when known,
                otherwise the broker's average when non-zero,
                otherwise the last close (flagged "provisional")
  * broker qty < ledger qty  -> add a reconciliation sell for the difference
  * broker-only symbol       -> add as a buy lot (provisional if no cost)
  * ledger-only symbol       -> add a sell to zero it out
Every adjustment is written to ledger.jsonl with source="reconcile" so it can be
audited, and never fabricates a real broker fill.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
LEDGER = BASE / "state" / "ledger.jsonl"
SNAP = BASE / "state" / "broker_portfolio.json"
BACKUP = BASE / "state" / "ledger.backup.jsonl"
REPORT = BASE / "state" / "ledger_sync.json"


def _ledger_rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    rows = []
    for line in LEDGER.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def _ledger_positions(rows: list[dict]) -> dict:
    pos = {}
    for r in rows:
        sym = r.get("symbol")
        if not sym:
            continue
        q, p = float(r.get("qty") or 0), float(r.get("price") or 0)
        if r.get("side") == "buy":
            b = pos.setdefault(sym, {"qty": 0.0, "cost": 0.0})
            b["qty"] += q
            b["cost"] += q * p
        elif r.get("side") == "sell":
            b = pos.setdefault(sym, {"qty": 0.0, "cost": 0.0})
            b["qty"] -= q
    return pos


def _last_close(symbol: str) -> float:
    fp = BASE.parent / "TeseRobot" / "data" / "daily" / f"{symbol}_daily.csv"
    try:
        import pandas as pd
        df = pd.read_csv(fp)
        return float(df["Close"].iloc[-1])
    except Exception:
        return 0.0


def main(apply: bool = True) -> int:
    if not SNAP.exists():
        print("no broker snapshot — run portfolio_sync.py first")
        return 1
    snap = json.loads(SNAP.read_text(encoding="utf-8"))
    broker = {p["symbol"]: p for p in snap.get("broker", [])}
    rows = _ledger_rows()
    pos = _ledger_positions(rows)

    adjustments = []
    for sym in sorted(set(broker) | set(pos)):
        b = broker.get(sym)
        l = pos.get(sym, {"qty": 0.0, "cost": 0.0})
        bq = float(b.get("qty") or 0) if b else 0.0
        lq = l["qty"]
        diff = bq - lq
        if abs(diff) < 1:
            continue
        avg_ledger = (l["cost"] / l["qty"]) if l["qty"] > 0 else 0.0
        price = (float(b.get("avg") or 0) if b else 0.0) or avg_ledger or _last_close(sym)
        provisional = not (float(b.get("avg") or 0) if b else 0.0) and not avg_ledger
        adj = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "symbol": sym,
            "side": "buy" if diff > 0 else "sell",
            "qty": int(abs(diff)),
            "price": round(price, 1),
            "source": "reconcile",
            "note": (f"ledger {lq:,.0f} -> broker {bq:,.0f}"
                     + (" (provisional price)" if provisional else "")),
            "provisional": bool(provisional),
        }
        adjustments.append(adj)

    print(json.dumps({"adjustments": adjustments, "applied": apply},
                     ensure_ascii=False, indent=1)[:1600])

    if apply and adjustments:
        BACKUP.write_text(LEDGER.read_text(encoding="utf-8"), encoding="utf-8")
        with LEDGER.open("a", encoding="utf-8") as fh:
            for a in adjustments:
                fh.write(json.dumps(a, ensure_ascii=False) + "\n")
        print(f"appended {len(adjustments)} reconciliation rows (backup: {BACKUP.name})")

    REPORT.write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                  "adjustments": adjustments, "applied": apply},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(apply="--dry" not in sys.argv))
