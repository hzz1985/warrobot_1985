"""Lot-based take-profit / stop-loss.

The user's point (1405/08/27): the first آواپارس purchase (82,542 @ 6,916 fee basis)
touched +25% intraday, but the bot measured P&L on the blended average of the
whole position after a later top-up (7,736) and therefore saw only ~+8.6%, so it
never sold. Measuring every purchase as its own lot fixes that.

Rules (unchanged strategy, corrected accounting):
    lot pnl% = price / (lot buy price incl. fee) - 1
    sell the quantity of every lot whose pnl% >= +25%  (take profit)
    sell the quantity of every lot whose pnl% <= -3%   (stop loss)
    افران / خساپا are never touched
"""
from __future__ import annotations

import json
import subprocess
import os
import sys
# (Hark fix #2) no forced login: pw/session.js now throttles instead of banning,
# and a forced login per run was what kept kicking the shared session.
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "src"))

JOURNAL = BASE / "state" / "trade_journal.jsonl"
PNL = BASE / "state" / "pnl.json"
OUT = BASE / "state" / "lot_exits.json"
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"

TP, SL = 25.0, 3.0
FEE = 0.89  # percent: matches the broker fee-inclusive basis (6,855 -> 6,916)
MANUAL_ONLY = {"افران", "خساپا"}


def open_lots() -> list[dict]:
    lots = []
    if JOURNAL.exists():
        for line in JOURNAL.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("status") != "open":
                continue
            if str(d.get("symbol")) in MANUAL_ONLY:
                continue
            qty = float(d.get("qty") or 0)
            px = float(d.get("price") or 0)
            if qty <= 0 or px <= 0:
                continue
            cost = px * (1 + FEE / 100.0)
            lots.append({"symbol": str(d["symbol"]), "qty": qty,
                         "buy": px, "cost": cost,
                         "trade_id": d.get("trade_id"), "at": d.get("at")})
    return lots


def main(dry: bool = True) -> int:
    try:
        pnl = json.loads(PNL.read_text(encoding="utf-8"))
    except Exception:
        pnl = {}
    global prices_cache
    prices = {str(p["symbol"]): float(p.get("price") or 0)
              for p in pnl.get("positions", [])}
    prices_cache = dict(prices)
    held = {str(p["symbol"]): float(p.get("qty") or 0)
            for p in pnl.get("positions", [])}

    lots = open_lots()
    due: dict[str, dict] = {}
    for lot in lots:
        price = prices.get(lot["symbol"], 0.0)
        if price <= 0:
            continue
        pct = (price / lot["cost"] - 1) * 100 if lot["cost"] else 0.0
        reason = "take_profit" if pct >= TP else "stop_loss" if pct <= -SL else None
        if not reason:
            continue
        d = due.setdefault(lot["symbol"], {"symbol": lot["symbol"], "qty": 0.0,
                                           "reason": reason, "pct": round(pct, 2),
                                           "lots": []})
        d["qty"] += lot["qty"]
        d["lots"].append({"qty": lot["qty"], "buy": lot["buy"], "pct": round(pct, 2)})

    for sym, d in due.items():
        d["qty"] = min(d["qty"], held.get(sym, d["qty"]))
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "dry": dry,
           "lots": len(lots), "due": list(due.values()), "placed": []}
    if not dry:
        out["placed"] = sell_due(out)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"lots": len(lots),
                      "due": [{k: v for k, v in d.items() if k != "lots"}
                              for d in out["due"]]}, ensure_ascii=False))
    return 0


def _reference_price(symbol: str) -> float:
    """Last known market price (session close / panel) to validate an order price."""
    try:
        import json as _j
        for path in (_j and (BASE / "state" / "webdata" / "prices.json"),
                     BASE / "state" / "prices.json"):
            try:
                d = _j.loads(path.read_text(encoding="utf-8"))
                sym = (d.get("symbols") or {}).get(symbol) or {}
                px = float(sym.get("close") or 0)
                if px > 0:
                    return px
            except Exception:
                continue
    except Exception:
        pass
    return 0.0


def resolve_isin(symbol: str) -> str:
    try:
        import tsetmc as api
        return str((api.resolve(symbol) or {}).get("isin") or "")
    except Exception:
        return ""


def sell_due(out: dict) -> list[dict]:
    """Place a sell order for every lot that reached its own TP/SL."""
    placed = []
    for d in out.get("due") or []:
        sym = d["symbol"]
        qty = int(d.get("qty") or 0)
        price = int(float(prices_cache.get(sym) or 0))
        # clamp the exit inside today's official band (TSETMC staticThreshold)
        try:
            import price_discovery as _pd
            _sp = _pd.sell_price(sym, 0.0, price)
            if _sp["price"] > 0:
                price = int(_sp["price"])
        except Exception:
            pass
        # reject a price that cannot be real (stale snapshot / parse error)
        try:
            import json as _j
            _pnl = _j.loads((BASE / "state" / "pnl.json").read_text(encoding="utf-8"))
            _avg = next((float(x.get("avg_cost") or 0) for x in _pnl.get("positions", [])
                         if x.get("symbol") == sym), 0.0)
        except Exception:
            _avg = 0.0
        _ref = _reference_price(sym)
        if _ref > 0 and abs(price / _ref - 1) > 0.10:
            placed.append({"symbol": sym, "qty": qty, "price": price,
                           "reason": "price_out_of_band", "reference": _ref,
                           "submitted": False})
            continue
        if _avg and not (0.5 * _avg <= price <= 2.0 * _avg):
            placed.append({"symbol": sym, "qty": qty, "price": price,
                           "reason": "price_out_of_band", "avg": _avg,
                           "submitted": False})
            continue
        isin = resolve_isin(sym)
        try:
            p = subprocess.run([NODE, str(PW / "flow.js"), sym, isin,
                                str(qty), str(price), "0", "sell"],
                               cwd=str(PW), capture_output=True, text=True,
                               timeout=1200, encoding="utf-8", errors="replace")
            data = {}
            for line in reversed((p.stdout or "").splitlines()):
                if line.strip().startswith("{"):
                    data = json.loads(line)
                    break
        except Exception as exc:
            data = {"message": f"error:{str(exc)[:80]}"}
        rec = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "symbol": sym, "qty": qty, "price": price,
               "reason": f"lot_{d.get('reason')}", "pct": d.get("pct"),
               "message": data.get("message"),
               "submitted": bool(data.get("submitted"))}
        placed.append(rec)
        try:
            (BASE / "state" / "exits_log.jsonl").open("a", encoding="utf-8").write(
                json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
        if rec["submitted"]:
            try:
                import trade_journal as tj
                tj.record_sell(sym, qty, price, reason=rec["reason"], at=rec["at"])
            except Exception:
                pass
    out["placed"] = placed
    return placed


prices_cache: dict = {}


if __name__ == "__main__":
    raise SystemExit(main(dry="--live" not in sys.argv))
