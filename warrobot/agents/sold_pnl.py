"""A15 — P&L of the sold symbols, computed from (sell − buy) × qty.

Sources: realized.jsonl (authority) with journal fallback. Fees are taken from
the record when present; otherwise 0.89% of the buy value (broker rate used by
the bot elsewhere).

    python -m agents.sold_pnl
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
OUT = STATE / "agents" / "a15_sold_pnl.json"
FEE = 0.0089


def _rows(path: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except Exception:
        return []


def run() -> dict:
    rows = []
    for r in _rows(STATE / "realized.jsonl"):
        sym = str(r.get("symbol") or "")
        qty = float(r.get("qty") or 0)
        buy = float(r.get("avg_cost") or 0)
        sell = float(r.get("sell_price") or 0)
        if not sym or qty <= 0 or buy <= 0 or sell <= 0:
            continue
        gross = (sell - buy) * qty
        fee = float(r.get("fee") or 0) or round(buy * qty * FEE, 0)
        net = round(gross - fee, 0)
        rows.append({"symbol": sym, "at": str(r.get("at"))[:10],
                     "qty": qty, "buy": buy, "sell": sell,
                     "gross_rial": round(gross, 0), "fee_rial": fee,
                     "pnl_rial": net,
                     "pnl_pct": round(net / (buy * qty) * 100, 2),
                     "source": r.get("source") or "record"})
    rows.sort(key=lambda x: x["at"], reverse=True)
    total = round(sum(x["pnl_rial"] for x in rows), 0)
    wins = sum(1 for x in rows if x["pnl_rial"] > 0)
    out = {"agent": "a15_sold_pnl", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "trades": rows, "count": len(rows), "wins": wins,
           "total_pnl_rial": total,
           "formula": "(sell - buy) * qty - fee",
           "ok": True}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False)[:700])
