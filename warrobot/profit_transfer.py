"""Central profit transfer to the fixed-income fund افران.

Every selling path (stop/take-profit, pre-open exits, sell monitor) must move the
realised profit into افران. This module is the single implementation so the rule
cannot drift between paths.

    from profit_transfer import transfer
    res = transfer(profit_rial=12_000_000, source="intraday_exit")
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
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))

NODE = r"C:\Program Files\nodejs\node.exe"
PW = BASE / "pw"
AFRAN = "افران"
AFRAN_ISIN = "IRT1AFRN0001"
MIN_ORDER_RIAL = 5_000_000
LOG = BASE / "state" / "profit_transfers.jsonl"


def _price() -> float:
    try:
        import tsetmc as api
        inst = api.resolve(AFRAN)
        if not inst:
            return 0.0
        rows = (api.closing_daily(inst["ins_code"], 5) or {}).get("closingPriceDaily") or []
        rows = sorted(rows, key=lambda r: str(r.get("dEven") or ""))
        return float(rows[-1].get("pClosing") or 0) if rows else 0.0
    except Exception:
        return 0.0


def transfer(profit_rial: float, source: str = "") -> dict:
    rec = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "source": source, "profit_rial": round(float(profit_rial or 0), 0)}
    if rec["profit_rial"] < MIN_ORDER_RIAL:
        rec["skipped"] = "profit below the minimum order value"
        return rec
    price = _price() or 56900.0
    qty = int(rec["profit_rial"] / (price * 1.015))
    if qty <= 0:
        rec["skipped"] = "qty=0"
        return rec
    try:
        import pw_lock
        with pw_lock.guard("profit_transfer") as got:
            if not got:
                rec["skipped"] = "playwright_lock_timeout"
                return rec
            p = subprocess.run([NODE, str(PW / "flow.js"), AFRAN, AFRAN_ISIN,
                                str(qty), str(price)],
                               cwd=str(PW), capture_output=True, text=True, timeout=900,
                               encoding="utf-8", errors="replace")
            data = {}
            for line in reversed((p.stdout or "").splitlines()):
                if line.strip().startswith("{"):
                    data = json.loads(line)
                    break
    except Exception as exc:
        rec["error"] = str(exc)[:100]
        return rec
    rec.update({"afran_qty": qty, "afran_price": price,
                "message": data.get("message"),
                "intended_price": data.get("intended_price"),
                "placed_price": data.get("placed_price"),
                "submitted": bool("موفق" in str(data.get("message") or ""))})
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


if __name__ == "__main__":
    amount = float(sys.argv[1]) if len(sys.argv) > 1 else 0
    print(json.dumps(transfer(amount, source="cli"), ensure_ascii=False))
