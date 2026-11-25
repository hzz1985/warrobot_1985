"""A12 — order-price auditor.

Reads the prices the broker actually recorded for today's orders and compares
them with what price discovery says they should be (today's ceiling / best ask
for buys, best bid for sells). Mismatches are reported, never auto-changed.

    python -m agents.order_price
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
OUT = STATE / "agents" / "order_price.json"


def _orders() -> list[dict]:
    try:
        p = subprocess.run([NODE, str(PW / "broker_client.js"), "open"], cwd=str(PW),
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return json.loads(line).get("orders") or []
    except Exception:
        pass
    return []


def _refs() -> dict:
    try:
        return json.loads((STATE / "prices.json").read_text(encoding="utf-8")).get("symbols") or {}
    except Exception:
        return {}


def _positions() -> dict:
    try:
        d = json.loads((STATE / "pnl.json").read_text(encoding="utf-8"))
        return {str(x.get("symbol")): x for x in d.get("positions", [])}
    except Exception:
        return {}


def run() -> dict:
    import price_discovery as pd
    rows, bad = [], []
    pos = _positions()
    refs = _refs()
    for o in _orders():
        sym = str(o.get("symbol") or "")
        side = "sell" if "فروش" in str(o.get("side") or "") else "buy"
        px = float(o.get("price") or 0)
        prev = float((refs.get(sym) or {}).get("close") or 0)
        try:
            disc = (pd.sell_price if side == "sell" else pd.buy_price)(sym, prev)
        except Exception as exc:
            disc = {"price": 0, "source": "error:" + str(exc)[:40]}
        ok = bool(px > 0 and disc.get("price") and abs(px - disc["price"]) <= max(2, px * 0.002))
        held = pos.get(sym) or {}
        item = {"symbol": sym, "side": side, "placed": px,
                "qty": o.get("qty"),
                "pnl_rial": float(held.get("pnl_rial") or 0) if held else None,
                "pnl_pct": float(held.get("pnl_pct") or 0) if held else None,
                "suggested": disc.get("price"), "source": disc.get("source"),
                "floor": disc.get("floor"), "ceiling": disc.get("ceiling"),
                "price_ok": ok}
        rows.append(item)
        if not ok:
            bad.append(item)
    out = {"agent": "order_price", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "orders": rows, "mismatch": bad, "ok": not bad}
    if bad:
        out["escalate"] = f"{len(bad)} order price(s) differ from discovery"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False)[:700])
