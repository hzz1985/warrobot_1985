"""Cancel open orders through the real broker endpoint.

Discovered (1405-08-19): POST /Web/V1/Order/Delete with a JSON ARRAY of order id
strings is the only working cancel path:
    body ["3026111000005007"]  ->  {"Data":[{"SuccessCancel":true,...}]}
Everything else (orderId object, DELETE verb) returns 400 InputDataNotValid.
"""
from __future__ import annotations

import pw_lock  # serialises Chrome on the shared profile

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


def open_orders() -> list[dict]:
    try:
        p = subprocess.run([NODE, str(PW / "open_orders.js")], cwd=str(PW),
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return json.loads(line).get("orders") or []
    except Exception:
        pass
    return []


def cancel(ids: list[str]) -> dict:
    if not ids:
        return {"ok": True, "note": "nothing to cancel"}
    try:
        p = subprocess.run([NODE, str(PW / "cancel_delete.js")] + ids, cwd=str(PW),
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return json.loads(line)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}
    return {"ok": False, "error": "no output"}


def cancel_all() -> dict:
    import pw_lock
    with pw_lock.guard("cancel_all") as got:
        if not got:
            return {"cancelled": 0, "error": "playwright_lock_timeout"}
        before = open_orders()
    ids = [str(o["orderid"]) for o in before]
    res = cancel(ids)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ids": ids, "result": res,
                             "orders": [{"symbol": o["symbol"], "qty": o["qty"],
                                         "price": o["price"]} for o in before]},
                            ensure_ascii=False) + "\n")
        after = open_orders()
        return {"cancelled": len(before) - len(after), "before": len(before),
                "after": len(after), "api": res.get("response") or res.get("error")}


if __name__ == "__main__":
    print(json.dumps(cancel_all(), ensure_ascii=False))
