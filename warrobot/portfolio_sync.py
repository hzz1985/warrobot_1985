"""Read the broker portfolio (positions) and compare with the bot's ledger.

The dashboard was showing positions from WarRobot's ledger only, so a holding
that exists at the broker (bought outside the bot, or removed by hand) did not
appear. This script pulls the real portfolio from the broker API and writes
state/broker_portfolio.json + state/portfolio_diff.json.
"""
from __future__ import annotations

import pw_lock  # serialises Chrome on the shared profile

import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

import broker_pw  # noqa: E402

OUT = BASE / "state" / "broker_portfolio.json"
DIFF = BASE / "state" / "portfolio_diff.json"


def broker_positions() -> list[dict]:
    """One Playwright round trip: login (if needed) + daily portfolio."""
    import subprocess
    p = subprocess.run([r"C:\Program Files\nodejs\node.exe", str(BASE / "pw" / "portfolio.js")],
                       cwd=str(BASE / "pw"), capture_output=True, text=True, timeout=600,
                       encoding="utf-8", errors="replace")
    for line in reversed((p.stdout or "").splitlines()):
        if line.strip().startswith("{"):
            try:
                data = json.loads(line)
            except Exception:
                continue
            return data.get("positions") or []
    return []


def broker_balance() -> dict:
    """Cash figures for the equity curve (RealBalance / AccountBalance)."""
    import subprocess
    try:
        p = subprocess.run([r"C:\Program Files\nodejs\node.exe",
                            str(BASE / "pw" / "portfolio.js")],
                           cwd=str(BASE / "pw"), capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return json.loads(line).get("balance") or {}
    except Exception:
        pass
    return {}


def ledger_positions() -> list[dict]:
    fp = BASE / "state" / "ledger.jsonl"
    if not fp.exists():
        return []
    buys, sells = {}, {}
    for line in fp.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        sym = r.get("symbol")
        if not sym:
            continue
        q, price = float(r.get("qty") or 0), float(r.get("price") or 0)
        if r.get("side") == "buy":
            b = buys.setdefault(sym, {"qty": 0.0, "cost": 0.0})
            b["qty"] += q
            b["cost"] += q * price
        elif r.get("side") == "sell":
            sells[sym] = sells.get(sym, 0.0) + q
    out = []
    for sym, b in buys.items():
        qty = b["qty"] - sells.get(sym, 0.0)
        if qty > 0:
            out.append({"symbol": sym, "qty": int(qty),
                        "avg": round(b["cost"] / b["qty"], 1) if b["qty"] else 0.0})
    return out


def main() -> int:
    import pw_lock as _pl
    with _pl.guard("portfolio_sync") as got:
        if not got:
            print('{"error":"playwright_lock_timeout"}')
            return 1
        broker = broker_positions()
        balance = broker_balance()
    ledger = ledger_positions()
    bmap = {p["symbol"]: p for p in broker}
    lmap = {p["symbol"]: p for p in ledger}

    only_broker = [s for s in bmap if s not in lmap]
    only_ledger = [s for s in lmap if s not in bmap]
    qty_mismatch = [s for s in bmap if s in lmap and int(bmap[s]["qty"]) != int(lmap[s]["qty"])]

    out = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "balance": balance,
        "cash": float((balance or {}).get("RealBalance") or 0),
        "broker": broker,
        "ledger": ledger,
        "only_in_broker": only_broker,
        "only_in_ledger": only_ledger,
        "qty_mismatch": qty_mismatch,
        "in_sync": not (only_broker or only_ledger or qty_mismatch),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    DIFF.write_text(json.dumps({k: out[k] for k in
                                ("at", "only_in_broker", "only_in_ledger",
                                 "qty_mismatch", "in_sync")},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "broker": [f"{p['symbol']} x{p['qty']}" for p in broker],
        "ledger": [f"{p['symbol']} x{p['qty']}" for p in ledger],
        "only_in_broker": only_broker,
        "only_in_ledger": only_ledger,
        "qty_mismatch": qty_mismatch,
        "in_sync": out["in_sync"],
    }, ensure_ascii=False))
    return 0 if out["in_sync"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
