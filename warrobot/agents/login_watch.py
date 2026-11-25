"""A1 — login/session watchdog.

Owns the recurring failure: "the bot could not get into the broker panel during
market hours". It checks, boundedly repairs, and reports. It never touches the
strategy.

    python -m agents.login_watch
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"
OUT = BASE / "state" / "agents" / "a1_login.json"


def _json_tail(text: str) -> dict:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {}


def _orphans() -> int:
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
              "Where-Object { $_.CommandLine -like '*pw-profile*' } | Measure-Object | "
              "Select-Object -ExpandProperty Count")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60)
        return int((out.stdout or "0").strip() or 0)
    except Exception:
        return -1


def run(repair: bool = True) -> dict:
    out = {"agent": "a1_login", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "orphans": _orphans(), "attempts": []}
    if out["orphans"] > 0 and repair:
        try:
            import pw_reset
            out["cleaned"] = pw_reset.kill_orphans()
        except Exception as exc:
            out["clean_error"] = str(exc)[:80]

    ok = False
    for i in range(1, 4):
        p = subprocess.run([NODE, str(PW / "balance.js")], cwd=str(PW),
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        d = _json_tail((p.stdout or "") + (p.stderr or ""))
        bal = d.get("account_balance")
        out["attempts"].append({"try": i, "ok": bool(bal), "balance": bal})
        if bal:
            ok = True
            break
        if repair:
            try:
                import pw_reset
                pw_reset.clean_locks()
            except Exception:
                pass
            time.sleep(8)

    out["ok"] = ok
    if not ok:
        out["escalate"] = "login_failed_after_3_attempts"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(repair="--no-repair" not in sys.argv), ensure_ascii=False))
