"""Record the realised P/L of the آردینه stop-loss sale, reconcile the ledger,
rebuild the P/L snapshot and make the curve include realised trades."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REALIZED = BASE / "state" / "realized.jsonl"

# the filled sell: qty x price from the broker order, cost from the advanced page
entry = {
    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "symbol": "آردینه",
    "qty": 22649,
    "avg_cost": 47984.0,
    "sell_price": 44660.0,
    "cost_value": 1086789616.0,
    "proceeds": 1002620759.0,      # cash actually credited (after fees)
    "pnl_rial": round(1002620759.0 - 1086789616.0, 0),
    "pnl_pct": round((1002620759.0 / 1086789616.0 - 1) * 100, 2),
    "reason": "stop_loss",
    "orderid": "3026111420005129",
}
REALIZED.parent.mkdir(parents=True, exist_ok=True)
with REALIZED.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
print("realised recorded:", json.dumps(entry, ensure_ascii=False)[:160])

# reconcile the ledger with the broker (removes آردینه, updates افران)
print("\nledger sync:")
p = subprocess.run([sys.executable, "-u", str(BASE / "ledger_sync.py")],
                   cwd=str(BASE), capture_output=True, text=True, timeout=600,
                   encoding="utf-8", errors="replace")
print(((p.stdout or "") + (p.stderr or ""))[-500:])

# rebuild the P/L snapshot (writes equity_history with cash)
print("\npnl rebuild:")
p = subprocess.run([sys.executable, "-u", str(BASE / "pnl.py")],
                   cwd=str(BASE), capture_output=True, text=True, timeout=900,
                   encoding="utf-8", errors="replace")
out = (p.stdout or "").strip()
print(out[-400:])
