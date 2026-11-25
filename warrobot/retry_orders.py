"""Retry today's candidates whose order did not register (empty broker message).

Runs the same Playwright flow per symbol, sized at one third of total assets,
and skips symbols that already have a successful order today.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

import pandas as pd  # noqa: E402

NODE = r"C:\Program Files\nodejs\node.exe"
PW = BASE / "pw"
CAND = BASE / "state" / "candidates.csv"
LOG = BASE / "state" / "orders_log.jsonl"


def done_today() -> set:
    today = []
    if LOG.exists():
        rows = []
        for line in LOG.read_text(encoding="utf-8").splitlines()[-40:]:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
        # the last successful block of the day
        for r in rows:
            if r.get("submitted"):
                today.append(str(r.get("symbol")))
    return set(today[-6:])


def free_balance() -> float:
    import order_candidates as oc
    return oc._free_balance()


def total_assets() -> float:
    import rebalance as rb
    return rb.total_assets()


def main() -> int:
    df = pd.read_csv(CAND)
    already = done_today()
    free = free_balance()
    total = total_assets() or free
    third = total / 3.0
    print(f"free={free:,.0f} total={total:,.0f} third={third:,.0f} already={sorted(already)}")
    if free < 6_000_000:
        print("free balance too low")
        return 1

    import order_candidates as oc
    for _, r in df.iterrows():
        sym = str(r["symbol"])
        if sym in already:
            print(f"{sym}: already ordered today")
            continue
        px = float(r.get("close") or 0)
        if px <= 0:
            print(f"{sym}: no price")
            continue
        qty = int(min(third, free * 0.95) / (px * 1.015))
        if qty <= 0:
            print(f"{sym}: qty=0")
            continue
        try:
            p = subprocess.run([NODE, str(PW / "flow.js"), sym, oc._isin(sym), str(qty), str(px)],
                               cwd=str(PW), capture_output=True, text=True, timeout=900,
                               encoding="utf-8", errors="replace")
            data = {}
            for line in reversed((p.stdout or "").splitlines()):
                if line.strip().startswith("{"):
                    data = json.loads(line)
                    break
        except Exception as exc:
            data = {"message": f"error:{str(exc)[:60]}"}
        msg = str(data.get("message") or "")
        ok = ("موفق" in msg) or ("ثبت" in msg)
        print(f"{sym}: qty={qty} -> {'OK' if ok else 'FAIL'} :: {msg[:70]}")
        rec = {"symbol": sym, "qty": qty, "price": px, "message": msg, "submitted": ok}
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
