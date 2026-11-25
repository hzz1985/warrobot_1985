"""A3 — web-app & data keeper.

Owns: "the web app is not up to date / the P&L number is wrong".
Checks freshness and internal consistency of the data the dashboard shows, repairs
by re-running the producer once, and reports what it did.

    python -m agents.webapp_watch
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
OUT = STATE / "agents" / "a3_webapp.json"
PY = sys.executable
URL = "http://127.0.0.1:5077/"


def _age_min(path: Path) -> float:
    try:
        return round((time.time() - path.stat().st_mtime) / 60, 1)
    except Exception:
        return -1


def _http() -> dict:
    try:
        with urllib.request.urlopen(URL, timeout=30) as r:
            body = r.read(200000).decode("utf-8", "replace")
        return {"status": r.status, "len": len(body),
                "has_positions": "پوزیشن" in body, "has_tabs": "گزارش" in body}
    except Exception as exc:
        return {"status": 0, "error": str(exc)[:100]}


def _pnl_consistency() -> dict:
    try:
        d = json.loads((STATE / "pnl.json").read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "error": "pnl.json unreadable"}
    bad = []
    for p in d.get("positions", []):
        price = float(p.get("price") or 0)
        avg = float(p.get("avg_cost") or 0)
        pct = float(p.get("pnl_pct") or 0)
        if price > 0 and avg > 0:
            calc = (price / avg - 1) * 100
            if abs(calc - pct) > 0.05:
                bad.append({"symbol": p.get("symbol"), "shown": pct,
                            "calc": round(calc, 2)})
    return {"ok": not bad, "mismatch": bad[:5], "at": d.get("at")}


def run(repair: bool = True) -> dict:
    out = {"agent": "a3_webapp", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "actions": []}
    out["http"] = _http()
    out["pnl_age_min"] = _age_min(STATE / "pnl.json")
    out["prices_age_min"] = _age_min(STATE / "prices.json")
    out["consistency"] = _pnl_consistency()

    need_pnl = (out["pnl_age_min"] < 0 or out["pnl_age_min"] > 24 * 60
                or not out["consistency"]["ok"])
    if repair and need_pnl:
        out["actions"].append("refresh pnl")
        subprocess.run([PY, str(BASE / "pnl.py")], cwd=str(BASE),
                       capture_output=True, text=True, timeout=1800,
                       encoding="utf-8", errors="replace")
        out["pnl_age_min"] = _age_min(STATE / "pnl.json")
        out["consistency"] = _pnl_consistency()
    if repair and (out["prices_age_min"] < 0 or out["prices_age_min"] > 720):
        out["actions"].append("refresh prices")
        subprocess.run([PY, str(BASE / "nightly_prices.py")], cwd=str(BASE),
                       capture_output=True, text=True, timeout=1800,
                       encoding="utf-8", errors="replace")
        out["prices_age_min"] = _age_min(STATE / "prices.json")

    # freshness budget depends on the clock: tight during the session, loose after
    try:
        import market_cal
        market_open_now = market_cal.market_day() and (9, 0) <= (
            market_cal.now_tehran().hour, market_cal.now_tehran().minute) <= (12, 35)
    except Exception:
        market_open_now = False
    out["market_open"] = market_open_now
    budget = 60 if market_open_now else 24 * 60
    ok = (out["http"].get("status") == 200 and out["consistency"]["ok"]
          and 0 <= out["pnl_age_min"] <= budget)
    out["ok"] = ok
    if not ok:
        out["escalate"] = "webapp/pnl still unhealthy after repair"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(repair="--no-repair" not in sys.argv), ensure_ascii=False)[:800])
