"""Session bootstrap — the one place allowed to (re)establish the broker session.

Policy that protects the operator's session:
  1. first try to import the operator's own Chrome cookies (no login, no kick);
  2. only if that fails, do ONE budgeted login inside the 07:00-10:00 window;
  3. outside that window never log in (scripts report login_blocked instead).

    python session_bootstrap.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
OUT = STATE / "session_bootstrap.json"
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"


def _json_tail(text: str) -> dict:
    for line in reversed((text or "").splitlines()):
        if line.strip().startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {}


def _balance() -> dict:
    p = subprocess.run([NODE, str(PW / "balance.js")], cwd=str(PW), capture_output=True,
                       text=True, timeout=600, encoding="utf-8", errors="replace")
    return _json_tail((p.stdout or "") + (p.stderr or ""))


def main() -> int:
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "steps": []}
    # 1) reuse whatever session exists (cookies loaded by balance.js itself)
    d = _balance()
    out["steps"].append({"session": bool(d.get("account_balance")),
                         "balance": d.get("account_balance"), "msg": d.get("message")})
    if d.get("account_balance"):
        out["ok"] = True
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False))
        return 0

    # 2) import the operator's Chrome session (no login)
    try:
        p = subprocess.run([sys.executable, str(BASE / "sync_login.py")], cwd=str(BASE),
                           capture_output=True, text=True, timeout=900,
                           encoding="utf-8", errors="replace")
        synced = _json_tail(p.stdout or "")
        out["steps"].append({"sync_login": synced})
    except Exception as exc:
        synced = {}
        out["steps"].append({"sync_login_error": str(exc)[:120]})
    if synced.get("status") or synced.get("ok"):
        d = _balance()
        out["steps"].append({"after_sync": bool(d.get("account_balance")),
                             "balance": d.get("account_balance")})
        if d.get("account_balance"):
            out["ok"] = True
            OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps(out, ensure_ascii=False))
            return 0

    # 3) last resort: one budgeted login (only inside the allowed window)
    try:
        import market_cal
        hh = market_cal.now_tehran().hour
        allowed = (7 <= hh < 10)
    except Exception:
        allowed = False
    if allowed:
        d = _balance()          # balance.js performs the login if the gate allows it
        out["steps"].append({"budgeted_login": bool(d.get("account_balance")),
                             "balance": d.get("account_balance")})
        out["ok"] = bool(d.get("account_balance"))
    else:
        out["ok"] = False
        out["steps"].append({"budgeted_login": "skipped_outside_window"})
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False)[:600])
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
