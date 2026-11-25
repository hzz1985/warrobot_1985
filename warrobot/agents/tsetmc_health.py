"""A17 — data-source health: band + 5-level book for the tracked symbols."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
OUT = STATE / "agents" / "a17_tsetmc.json"


def run() -> dict:
    import price_discovery as pd
    symbols = []
    try:
        import csv
        with (STATE / "candidates.csv").open(encoding="utf-8-sig") as fh:
            for row in list(csv.DictReader(fh))[:5]:
                symbols.append((row["symbol"], float(row.get("close") or 0)))
    except Exception:
        pass
    rows, bad = [], 0
    for sym, close in symbols:
        try:
            b = pd.buy_price(sym, close)
            ok = b["price"] > 0 and b["ceiling"] > 0
            if not ok:
                bad += 1
            rows.append({"symbol": sym, "price": b["price"], "floor": b["floor"],
                         "ceiling": b["ceiling"], "source": b["source"],
                         "best_bid": b["best_bid"], "best_ask": b["best_ask"], "ok": ok})
        except Exception as exc:
            bad += 1
            rows.append({"symbol": sym, "ok": False, "error": str(exc)[:60]})
    out = {"agent": "a17_tsetmc", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "symbols": rows, "ok": bad == 0}
    if bad:
        out["escalate"] = f"{bad} symbol(s) without band/book"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False)[:600])
