"""Python bridge to the Playwright order path (the only one that works).

The browser session does not survive between runs, so one call does the whole
thing: login (OCR) -> bind the symbol -> read the panel price and the ticket
limit -> size on the FREE balance -> click buy -> report.

Usage:
    python broker_pw.py order <symbol> <isin> [qty] [price]
    python broker_pw.py check
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"
OUT = BASE / "state" / "last_order_result.json"


def _clear_profile_lock() -> None:
    """A crashed Chrome leaves a SingletonLock that blocks the next launch."""
    prof = BASE / "state" / "pw-profile"
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (prof / name).unlink(missing_ok=True)
        except Exception:
            pass


def _run(script: str, args: list[str], timeout: int = 600) -> dict:
    _clear_profile_lock()
    import pw_lock
    with pw_lock.guard(f"broker_pw:{script}") as got:
        if not got:
            return {"ok": False, "reason": "playwright_lock_timeout"}
    try:
        p = subprocess.run([NODE, str(PW / script)] + args, cwd=str(PW),
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "timeout"}
    raw = (p.stdout or "").strip().splitlines()
    for line in reversed(raw):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                data.setdefault("ok", bool(data.get("message") and "موفق" in str(data.get("message"))))
                return data
            except Exception:
                continue
    return {"ok": False, "reason": f"no_output rc={p.returncode}", "stderr": (p.stderr or "")[-200:]}


def order(symbol: str, isin: str, qty: int = 0, price: float | None = None) -> dict:
    args = [symbol, isin, str(int(qty))]
    if price:
        args.append(str(price))
    data = _run("flow.js", args, timeout=900)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def check() -> dict:
    data = _run("check2.js", [], timeout=600)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def free_balance() -> float:
    """Free cash (AccountBalance), the number orders must be sized on."""
    data = check()
    for line in str(data.get("raw") or "").splitlines():
        pass
    import re
    m = re.search(r'"AccountBalance":([\d.]+)', json.dumps(data, ensure_ascii=False))
    return float(m.group(1)) if m else 0.0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "order":
        sym = sys.argv[2]
        isin = sys.argv[3]
        qty = int(sys.argv[4]) if len(sys.argv) > 4 else 0
        price = float(sys.argv[5]) if len(sys.argv) > 5 else None
        print(json.dumps(order(sym, isin, qty, price), ensure_ascii=False)[:900])
    else:
        print(json.dumps(check(), ensure_ascii=False)[:900])
