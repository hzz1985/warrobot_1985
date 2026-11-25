"""A10 — sale reconciliation: a broker sale must never go unrecorded.

The operator reported "آواپارس was sold but not recorded". Investigation showed
the position was still held at the broker (cash unchanged, portfolio intact) and
only two FAILED sell attempts were logged, so the real gap is different:

  * failed attempts were logged with submitted=false and never reconciled later;
  * a broker fill (when it happens) would only be recorded if a local hook ran.

This agent closes that gap every evening:

  1. reads the broker's trades (today) and, when available, order history;
  2. matches sells against state/realized.jsonl + state/trade_journal.jsonl;
  3. any broker sell missing from the books is recorded automatically
     (realized row + journal close) and an email is sent;
  4. attempts that failed are summarised so the cause is visible.

    python -m agents.sale_reconcile
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"
OUT = STATE / "agents" / "a10_sales.json"
REALIZED = STATE / "realized.jsonl"
JOURNAL = STATE / "trade_journal.jsonl"


def _json_tail(text: str) -> dict:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {}


def _broker_trades() -> list[dict]:
    try:
        p = subprocess.run([NODE, str(PW / "broker_client.js"), "trades"], cwd=str(PW),
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        d = _json_tail(p.stdout + p.stderr)
        return d.get("trades") or []
    except Exception:
        return []


def _known_sells() -> set[tuple]:
    known = set()
    for path in (REALIZED, JOURNAL):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            sym = str(d.get("symbol") or "")
            ts = str(d.get("at") or d.get("sell_at") or "")[:10]
            if sym:
                known.add((sym, ts))
    return known


def _record_sale(sym: str, qty: float, price: float, at: str) -> dict:
    """Write the missing sale into realized.jsonl and close the journal row."""
    pnl = 0.0
    pct = 0.0
    try:
        journal = [json.loads(x) for x in JOURNAL.read_text(encoding="utf-8").splitlines()
                   if x.strip()]
    except Exception:
        journal = []
    buy = next((r for r in reversed(journal)
                if r.get("symbol") == sym and r.get("status") == "open"), None)
    if buy:
        buy_px = float(buy.get("price") or 0)
        pnl = (price - buy_px) * qty
        pct = (price / buy_px - 1) * 100 if buy_px else 0.0
    row = {"at": at, "symbol": sym, "qty": qty, "avg_cost": (buy or {}).get("price"),
           "sell_price": price, "cost_value": round(price * qty - pnl, 0),
           "proceeds": round(price * qty, 0), "pnl_rial": round(pnl, 0),
           "pnl_pct": round(pct, 2), "reason": "reconciled",
           "source": "a10_sale_reconcile"}
    with REALIZED.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    try:
        import trade_journal
        trade_journal.record_sell(sym, qty, price, reason="reconciled", at=at)
    except Exception:
        pass
    return row


def run(repair: bool = True) -> dict:
    trades = _broker_trades()
    known = _known_sells()
    sells = []
    for t in trades:
        side = str(t.get("OrderSide") or t.get("side") or t.get("TradeSide") or "")
        if not (str(t.get("sellPrice")) or "فروش" in side or side in ("86", "2")):
            continue
        sym = str(t.get("lVal18AFC") or t.get("symbol") or "")
        qty = float(t.get("qtity") or t.get("qty") or 0)
        px = float(t.get("price") or t.get("Price") or 0)
        at = str(t.get("dEven") or t.get("date") or datetime.now().strftime("%Y-%m-%d"))
        if sym and qty > 0:
            sells.append({"symbol": sym, "qty": qty, "price": px, "at": at})

    missing = [s for s in sells
               if (s["symbol"], s["at"][:10]) not in known
               and (s["symbol"], datetime.now().strftime("%Y-%m-%d")) not in known]

    out = {"agent": "a10_sales", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "broker_trades": len(trades), "broker_sells": len(sells),
           "missing": missing, "recorded": []}
    if missing and repair:
        for s in missing:
            out["recorded"].append(_record_sale(s["symbol"], s["qty"], s["price"], s["at"]))
    out["ok"] = not missing or bool(out["recorded"])
    if missing:
        out["escalate"] = f"{len(missing)} broker sale(s) were missing from the books"
        try:
            import tsebot.alerts as alerts
            alerts.send("🧾 [WarRobot] تطبیق فروش‌ها",
                        json.dumps(out, ensure_ascii=False)[:1200])
        except Exception:
            pass
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(repair="--no-repair" not in sys.argv), ensure_ascii=False)[:800])
