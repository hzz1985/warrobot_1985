"""A16 — cash guard: keeps free cash from drifting above/below one target slot."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
OUT = STATE / "agents" / "a16_cash.json"


def run() -> dict:
    def _load(n):
        try:
            return json.loads((STATE / n).read_text(encoding="utf-8"))
        except Exception:
            return {}
    bal = _load("webdata/balance.json")
    pnl = _load("pnl.json")
    cash = float(bal.get("account_balance") or 0)
    holdings = round(sum(float(p.get("value") or 0) for p in pnl.get("positions", [])
                         if str(p.get("symbol")) != "افران"), 0)
    total = cash + holdings
    target = total / 3 if total else 0
    idle = cash > target * 1.25
    out = {"agent": "a16_cash", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "cash": cash, "holdings": holdings, "total": round(total, 0),
           "target_slot": round(target, 0),
           "idle_cash_pct": round(cash / total * 100, 2) if total else 0,
           "ok": True,
           "note": "نقد بیش از یک اسلات بیکار است" if idle else "نقد در محدودهٔ یک اسلات"}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False)[:500])
