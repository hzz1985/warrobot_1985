"""Cancel every open order (frees the blocked cash).

Tries the broker's cancel API first, falls back to the panel click.
Verifies the open-order list is empty afterwards.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
NODE = r"C:\Program Files\nodejs\node.exe"
PW = BASE / "pw"
LOG = BASE / "state" / "cancel_log.jsonl"


def _node(script: str, args: list[str] | None = None, timeout: int = 600) -> dict:
    try:
        p = subprocess.run([NODE, str(PW / script)] + list(args or []), cwd=str(PW),
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return json.loads(line)
    except Exception as exc:
        return {"error": str(exc)[:120]}
    return {}


def orders() -> list[dict]:
    return _node("open_orders.js").get("orders") or []


def cancel_via_api(order: dict) -> dict:
    return _node("cancel_api.js", [str(order.get("orderid") or ""),
                                   str(order.get("symbol") or "")], timeout=420)


def cancel_via_panel(order: dict) -> dict:
    try:
        p = subprocess.run([NODE, str(PW / "cancel_order.js"), str(order["symbol"]),
                            str(order["qty"])],
                           cwd=str(PW), capture_output=True, text=True, timeout=420,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "").strip()
        return {"panel": out[-80:]}
    except Exception as exc:
        return {"panel_error": str(exc)[:100]}


def main() -> int:
    before = orders()
    print(f"open orders before: {len(before)}")
    results = []
    for o in before:
        res = cancel_via_api(o)
        ok = bool(res.get("ok"))
        if not ok:
            res = {**res, **cancel_via_panel(o)}
        rec = {"symbol": o["symbol"], "orderid": o["orderid"], "qty": o["qty"],
               "price": o["price"], "api": res}
        results.append(rec)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"  {o['symbol']} {o['orderid']} -> {json.dumps(res, ensure_ascii=False)[:160]}")

    after = orders()
    print(f"open orders after : {len(after)}")
    print(json.dumps({"before": len(before), "after": len(after),
                      "freed": len(before) - len(after)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
