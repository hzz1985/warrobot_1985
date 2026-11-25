"""Nightly trade-ledger capture from the broker (fills with commissions).

The report tab needs every closed trade with its buy and sell time/price, so the
bot appends the broker's own trade rows to state/trades_ledger.jsonl every night.
The broker endpoint only returns the current day, which is exactly why this must
run daily — history is accumulated here, not fetched later.

Dedupe key: (id | symbol+time+side+qty+price).
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE))
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"
LEDGER = BASE / "state" / "trades_ledger.jsonl"
RAW = BASE / "state" / "trades_raw.json"


def pick(row: dict, *names, default=None):
    for n in names:
        for k, v in row.items():
            if str(k).lower() == n.lower() and v not in (None, ""):
                return v
    return default


def normalise(row: dict) -> dict | None:
    sym = pick(row, "symbol", "Symbol", "lval18AFC", "instrument")
    qty = pick(row, "qty", "quantity", "OrderQty", "orderqty", "TradeQuantity", "Quantity")
    px = pick(row, "price", "Price", "orderprice", "TradePrice", "tradePrice")
    side = pick(row, "side", "Side", "orderSide", "TradeSide", "BuySell", default="")
    fee = pick(row, "commission", "Commission", "fee", "Fee", "brokerage", default=0)
    when = pick(row, "time", "Time", "date", "Date", "tradeDate", "dt", "TradeDate")
    tid = pick(row, "id", "Id", "tradeid", "TradeId", "OrderId")
    if not sym or not qty or not px:
        return None
    side_txt = str(side)
    if side_txt in ("65", "1", "buy", "خرید"):
        side_txt = "buy"
    elif side_txt in ("86", "2", "sell", "فروش"):
        side_txt = "sell"
    try:
        qty = float(str(qty).replace(",", ""))
        px = float(str(px).replace(",", ""))
        fee = float(str(fee).replace(",", "") or 0)
    except Exception:
        return None
    return {"at": str(when or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))[:19],
            "symbol": str(sym).strip(), "side": str(side_txt).lower() or "?",
            "qty": qty, "price": px, "fee": fee, "trade_id": str(tid or ""),
            "source": "broker"}


def main() -> int:
    try:
        p = subprocess.run([NODE, str(PW / "trades_full.js")], cwd=str(PW),
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        data = {}
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                data = json.loads(line)
                break
        rows = data.get("rows") or []
    except Exception as exc:
        rows = []
        print("trades error:", str(exc)[:120])

    RAW.write_text(json.dumps(rows, ensure_ascii=False, indent=1)[:200000], encoding="utf-8")
    known = set()
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                known.add((d.get("trade_id"), d.get("symbol"), d.get("at"),
                           d.get("side"), d.get("qty"), d.get("price")))
            except Exception:
                pass
    added = 0
    with LEDGER.open("a", encoding="utf-8") as fh:
        for r in rows:
            n = normalise(r if isinstance(r, dict) else {})
            if not n:
                continue
            key = (n["trade_id"], n["symbol"], n["at"], n["side"], n["qty"], n["price"])
            if key in known:
                continue
            known.add(key)
            fh.write(json.dumps(n, ensure_ascii=False) + "\n")
            added += 1
    print(json.dumps({"raw_rows": len(rows), "added": added,
                      "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
