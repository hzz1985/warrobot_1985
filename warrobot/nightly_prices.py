"""Nightly snapshot: buying power from the broker + final prices for everything.

The 1405/08/24 incident showed how expensive a stale price can be, so every
night after the scan we store one authoritative file:

    state/prices.json
        at            : when it was taken
        balance       : AccountBalance / RealBalance / BlockedBalance / PurchasingPower
        symbols       : symbol -> {close, session, source, isin, holding}

Price sources, in order of trust:
  1) broker panel (bneeded for holdings; that is the price we trade against)
  2) the local daily file (TeseRobot/data/daily), whose last session is the
     official close we will use at the next pre-open
  3) never: the TSETMC closing_daily default page (it was 6 weeks stale)
"""
from __future__ import annotations

import csv
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
TESE = BASE.parent / "TeseRobot"
NODE = r"C:\Program Files\nodejs\node.exe"
PW = BASE / "pw"
OUT = BASE / "state" / "prices.json"
CAND = BASE / "state" / "candidates.csv"
PNL = BASE / "state" / "pnl.json"


def broker_balance() -> dict:
    """AccountBalance straight from the broker panel (retried: locks/captcha)."""
    last_err = ""
    for attempt in range(3):
        try:
            p = subprocess.run([NODE, str(PW / "balance.js")], cwd=str(PW),
                               capture_output=True, text=True, timeout=600,
                               encoding="utf-8", errors="replace")
            data = {}
            for line in reversed((p.stdout or "").splitlines()):
                if line.strip().startswith("{"):
                    data = json.loads(line)
                    break
            if data.get("account_balance"):
                return {k: data.get(k) for k in
                        ("account_balance", "real_balance", "blocked_balance",
                         "purchasing_power", "bourse_code", "title")}
            last_err = (p.stderr or p.stdout or "empty")[-160:]
        except Exception as exc:
            last_err = str(exc)[:160]
        import time
        time.sleep(20)
    return {"error": last_err}


def panel_prices() -> dict:
    """Live prices of the holdings from the broker snapshot."""
    try:
        d = json.loads(PNL.read_text(encoding="utf-8"))
        return {str(r["symbol"]): float(r.get("price") or 0)
                for r in d.get("positions", []) if r.get("price")}
    except Exception:
        return {}


def local_close(symbol: str) -> tuple[float, str]:
    """Last close from the local daily file and its session date."""
    fp = TESE / "data" / "daily" / f"{symbol}_daily.csv"
    if not fp.exists():
        return 0.0, ""
    try:
        with fp.open(encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            return 0.0, ""
        last = rows[-1]
        return float(last.get("Close") or 0), str(last.get("date") or "")
    except Exception:
        return 0.0, ""


def candidates() -> list[dict]:
    if not CAND.exists():
        return []
    with CAND.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bal = broker_balance()
    pp = panel_prices()
    cands = candidates()

    symbols: dict[str, dict] = {}

    # buy candidates: official close from the local daily file
    for c in cands:
        sym = str(c.get("symbol") or "").strip()
        if not sym:
            continue
        close, session = local_close(sym)
        symbols[sym] = {
            "isin": str(c.get("isin") or ""),
            "holding": sym in pp,
            "close": round(close, 0),
            "session": session,
            "source": "daily_file",
            "reason": str(c.get("reason") or ""),
            "rank": c.get("pick_rank"),
        }

    # holdings: panel price wins (that is the price we trade against)
    for sym, px in pp.items():
        rec = symbols.setdefault(sym, {"isin": "", "holding": True})
        rec.update({"holding": True, "close": round(px, 0), "source": "broker_panel",
                    "session": at[:10]})

    stale = [s for s, r in symbols.items()
             if r.get("session") and r["session"] < "1405-08-20"]

    payload = {
        "at": at,
        "balance": bal,
        "symbols": symbols,
        "count": len(symbols),
        "candidates": len(cands),
        "stale_symbols": stale,
        "policy": "holdings -> broker panel · candidates -> last official close",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"at": at, "balance": bal, "symbols": len(symbols),
                      "stale": stale[:5]}, ensure_ascii=False))
    print("saved:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
