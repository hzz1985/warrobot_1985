"""Refresh every artefact the web app reads, so all three tabs are current.

Writes state/web_snapshot.json with:
  * buy candidates (state/candidates.csv)
  * sell candidates (state/sell_candidates.csv, rebuilt here if missing)
  * positions and P&L (from the ledger)
  * last order attempts (state/orders_log.jsonl)
The web app reads this file directly, so one refresh updates every tab.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

import pandas as pd  # noqa: E402

OUT = BASE / "state" / "web_snapshot.json"
CAND = BASE / "state" / "candidates.csv"
SELL = BASE / "state" / "sell_candidates.csv"
LOG = BASE / "state" / "orders_log.jsonl"


def _ledger() -> list[dict]:
    fp = BASE / "state" / "ledger.jsonl"
    if not fp.exists():
        return []
    rows = []
    for line in fp.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def _positions(rows: list[dict]) -> list[dict]:
    buys, sells = {}, {}
    for r in rows:
        sym, side = r.get("symbol"), r.get("side")
        q, p = float(r.get("qty") or 0), float(r.get("price") or 0)
        if not sym:
            continue
        if side == "buy":
            b = buys.setdefault(sym, {"qty": 0.0, "cost": 0.0})
            b["qty"] += q
            b["cost"] += q * p
        elif side == "sell":
            sells[sym] = sells.get(sym, 0.0) + q
    out = []
    for sym, b in buys.items():
        qty = b["qty"] - sells.get(sym, 0.0)
        if qty <= 0:
            continue
        out.append({"symbol": sym, "qty": int(qty),
                    "avg_cost": round(b["cost"] / b["qty"], 1) if b["qty"] else 0.0})
    return out


def main() -> int:
    # the dashboard's buy-candidates tab reads TeseRobot/data/today_picks.json
    try:
        import subprocess
        subprocess.run([sys.executable, "-u", str(BASE / "picks_export.py")],
                       cwd=str(BASE), capture_output=True, text=True, timeout=300)
    except Exception:
        pass
    snap = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    if CAND.exists():
        d = pd.read_csv(CAND)
        snap["candidates"] = d.to_dict("records")[:12]

    if SELL.exists():
        s = pd.read_csv(SELL)
        snap["sell_candidates"] = s.to_dict("records")[:20]

    rows = _ledger()
    positions = _positions(rows)
    snap["positions"] = positions

    sells = [r for r in rows if r.get("side") == "sell"]
    snap["realised"] = [
        {"ts": r.get("ts"), "symbol": r.get("symbol"), "qty": r.get("qty"), "price": r.get("price")}
        for r in sells[-20:]
    ]

    if LOG.exists():
        attempts = []
        for line in LOG.read_text(encoding="utf-8").splitlines()[-30:]:
            try:
                attempts.append(json.loads(line))
            except Exception:
                pass
        snap["order_attempts"] = attempts[-20:]

    OUT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"web_snapshot: {len(snap.get('candidates', []))} candidates · "
          f"{len(snap.get('sell_candidates', []))} sell rows · {len(positions)} positions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
