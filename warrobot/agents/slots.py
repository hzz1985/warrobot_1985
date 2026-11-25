"""A13 — three-slot allocation monitor.

The contract is equal weight: every slot is one third of total assets. This agent
checks, every half hour during the session, how far each slot deviates from that
third and flags anything beyond the tolerance.

    python -m agents.slots
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
OUT = STATE / "agents" / "a13_slots.json"
TOL = 0.05          # 5% of total assets
TARGET_SLOTS = 3


def _load(name: str) -> dict:
    try:
        return json.loads((STATE / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def run() -> dict:
    pnl = _load("pnl.json")
    bal = _load("webdata/balance.json")
    cash = float(bal.get("account_balance") or 0)
    held = []
    for p in pnl.get("positions", []):
        sym = str(p.get("symbol") or "")
        val = float(p.get("value") or 0)
        if sym and sym != "افران":
            held.append({"symbol": sym, "value": val,
                         "pnl_pct": float(p.get("pnl_pct") or 0)})
    open_orders = []
    try:
        import subprocess
        pw = BASE / "pw"
        r = subprocess.run([r"C:\Program Files\nodejs\node.exe",
                            str(pw / "broker_client.js"), "open"], cwd=str(pw),
                           capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace")
        for line in reversed((r.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                open_orders = json.loads(line).get("orders") or []
                break
    except Exception:
        pass
    reserved = sum(float(o.get("price") or 0) * float(o.get("qty") or 0)
                   for o in open_orders)
    total = cash + sum(h["value"] for h in held) + reserved
    target = total / TARGET_SLOTS if total else 0.0
    slots = []
    for h in held:
        dev = (h["value"] - target) / target * 100 if target else 0.0
        slots.append({**h, "kind": "filled", "deviation_pct": round(dev, 2),
                      "within_tolerance": abs(h["value"] - target) <= TOL * total})
    for o in open_orders:
        v = float(o.get("price") or 0) * float(o.get("qty") or 0)
        dev = (v - target) / target * 100 if target else 0.0
        slots.append({"symbol": str(o.get("symbol")), "value": round(v, 0),
                      "kind": "pending", "deviation_pct": round(dev, 2),
                      "within_tolerance": abs(v - target) <= TOL * total})
    free_slots = max(0, TARGET_SLOTS - len(held) - len(open_orders))
    out = {"agent": "a13_slots", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "total_assets": round(total, 0), "target_per_slot": round(target, 0),
           "cash": cash, "reserved": round(reserved, 0),
           "slots": slots, "open_slots": free_slots,
           "ok": True,
           "deviation": [s for s in slots if not s["within_tolerance"]],
           "tolerance_pct": TOL * 100}
    if out["deviation"] or free_slots:
        out["note"] = "انحراف اسلات‌ها گزارش شد؛ تصمیم با مالک است"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False)[:900])
