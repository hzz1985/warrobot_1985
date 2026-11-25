"""A2 — pre-open chain watchdog.

Owns: "the 08:45:01 order did not happen again". It verifies the artifacts of
each morning step, re-runs a missing step once (bounded), and escalates when the
chain still produced no order.

    python -m agents.preopen_watch
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "src"))
STATE = BASE / "state"
OUT = STATE / "agents" / "a2_preopen.json"
PY = sys.executable
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _submitted_today() -> int:
    n = 0
    p = STATE / "orders_log.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if str(d.get("at") or "")[:10] == _today() and d.get("submitted"):
                n += 1
    return n


def _open_orders() -> int:
    try:
        p = subprocess.run([NODE, str(PW / "broker_client.js"), "open"], cwd=str(PW),
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return len(json.loads(line).get("orders") or [])
    except Exception:
        pass
    return -1


def run(repair: bool = True) -> dict:
    out = {"agent": "a2_preopen", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "checks": {}, "actions": []}
    day = _today()

    rehearsal = _load(STATE / "rehearsal.json")
    ready = _load(STATE / "preopen_ready.json")
    out["checks"]["rehearsal"] = {"at": rehearsal.get("at"),
                                  "passed": rehearsal.get("passed"),
                                  "total": rehearsal.get("total")}
    out["checks"]["preready"] = {"at": ready.get("at"),
                                 "passed": ready.get("passed"),
                                 "total": ready.get("total")}

    # re-run a missing/failed practice exactly once
    if repair and str(rehearsal.get("at") or "")[:10] != day:
        out["actions"].append("run rehearsal")
        subprocess.run([PY, str(BASE / "rehearse_morning.py")], cwd=str(BASE),
                       capture_output=True, text=True, timeout=1800,
                       encoding="utf-8", errors="replace")
        rehearsal = _load(STATE / "rehearsal.json")

    submitted = _submitted_today()
    opened = _open_orders()
    out["orders"] = {"submitted_today": submitted, "open_orders": opened}
    out["ok"] = bool(rehearsal.get("passed") == rehearsal.get("total")
                     and (rehearsal.get("total") or 0) > 0)
    in_session = False
    try:
        import market_cal
        hh, mm = market_cal.now_tehran().hour, market_cal.now_tehran().minute
        in_session = market_cal.market_day() and (9, 0) <= (hh, mm) <= (12, 30)
    except Exception:
        pass
    out["in_session"] = in_session
    if submitted == 0 and opened == 0 and in_session:
        out["pending_order"] = True
    if out.get("pending_order") and repair and False:
        out["actions"].append("run order_candidates once")
        p = subprocess.run([PY, str(BASE / "order_candidates.py")], cwd=str(BASE),
                           capture_output=True, text=True, timeout=1800,
                           encoding="utf-8", errors="replace")
        out["actions"].append({"order_rc": p.returncode,
                               "tail": (p.stdout or "")[-200:]})
    if out["pending_order"]:
        out["escalate"] = "no order and no open order today"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(repair="--no-repair" not in sys.argv), ensure_ascii=False)[:800])
