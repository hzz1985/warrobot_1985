"""08:40 pre-open readiness: login retries + every number the order needs.

The contract says: at 08:40 the bot must (a) get into the broker panel, (b) know
the final prices of today's buy candidates, (c) know the P&L of every holding,
(d) know the buying power — and it must do that without any human.

Attempt policy
--------------
    login attempts : up to 5, 15 s apart (inside the 08:40 window)
    each attempt   : balance.js (login + AccountBalance)
    after success  : refresh P&L (pnl.py) and the price snapshot
                     (nightly_prices.py), then verify every item

Output: state/preopen_ready.json with one line per check, plus an email when
something critical is missing. Exit code 0 only when the bot is truly ready.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))
sys.path.insert(0, str(BASE.parent / "TeseRobot" / "src"))
sys.path.insert(0, str(BASE.parent / "TeseRobot"))

PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"
PY = sys.executable
OUT = BASE / "state" / "preopen_ready.json"
MAX_LOGIN_ATTEMPTS = 5
RETRY_DELAY = 15


def run(cmd: list[str], timeout: int = 900, cwd: Path | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(cwd or BASE), capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:
        return 1, str(exc)


def last_json(text: str) -> dict:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {}


def login_with_retries() -> dict:
    attempts = []
    for i in range(1, MAX_LOGIN_ATTEMPTS + 1):
        rc, out = run([NODE, str(PW / "balance.js")], cwd=PW)
        d = last_json(out)
        ok = rc == 0 and bool(d.get("account_balance"))
        attempts.append({"attempt": i, "ok": ok,
                         "balance": d.get("account_balance"),
                         "at": datetime.now().strftime("%H:%M:%S")})
        if ok:
            return {"ok": True, "attempts": attempts, "balance": d}
        if i < MAX_LOGIN_ATTEMPTS:
            try:
                import pw_reset
                pw_reset.kill_orphans()
            except Exception:
                pass
            time.sleep(RETRY_DELAY)
    return {"ok": False, "attempts": attempts}


def main() -> int:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:250]})
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

    # 1) login with retries
    login = login_with_retries()
    add(f"ورود کارگزار (تا {MAX_LOGIN_ATTEMPTS} تلاش)", login["ok"],
        f"تلاش‌ها={len(login['attempts'])}")

    # 2) refresh P&L of the holdings
    rc, out = run([PY, str(BASE / "pnl.py")], timeout=1800)
    pnl = last_json(out) if rc == 0 else {}
    try:
        pnl_doc = json.loads((BASE / "state" / "pnl.json").read_text(encoding="utf-8"))
    except Exception:
        pnl_doc = {}
    pos = pnl_doc.get("positions") or []
    priced = [p for p in pos if float(p.get("price") or 0) > 0]
    add("سود و زیان نمادهای پرتفوی", rc == 0 and len(priced) == len(pos) and len(pos) > 0,
        f"{len(priced)}/{len(pos)} قیمت‌دار · at={pnl_doc.get('at')}")

    # 3) refresh balance + final prices
    rc, out = run([PY, str(BASE / "nightly_prices.py")], timeout=1800)
    snap = last_json(out)
    add("قدرت خرید و قیمت پایانی (snapshot)", rc == 0 and bool((snap.get("balance") or {})
                                                              .get("account_balance")),
        f"balance={(snap.get('balance') or {}).get('account_balance')}")

    # 4) candidate closing prices present and fresh
    try:
        import csv as _csv
        with (BASE / "state" / "candidates.csv").open(encoding="utf-8-sig") as fh:
            cands = list(_csv.DictReader(fh))
    except Exception:
        cands = []
    try:
        prices = json.loads((BASE / "state" / "prices.json").read_text(encoding="utf-8")).get("symbols") or {}
    except Exception:
        prices = {}
    missing = []
    for c in cands:
        sym = str(c.get("symbol") or "")
        px = (prices.get(sym) or {}).get("close") or 0
        if not sym or float(px or 0) <= 0:
            missing.append(sym or "?")
    add("قیمت پایانی کاندیداهای خرید", len(cands) > 0 and not missing,
        f"{len(cands) - len(missing)}/{len(cands)}" + (f" · بدون قیمت: {missing[:5]}" if missing else ""))

    # 5) buying power
    bal = (snap.get("balance") or {}).get("account_balance") or 0
    add("قدرت خرید > ۵ میلیون ریال", float(bal or 0) > 5_000_000, f"{bal}")

    failed = [c for c in checks if not c["ok"]]
    payload = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "passed": len(checks) - len(failed), "total": len(checks),
               "login_attempts": login["attempts"],
               "candidates": len(cands), "positions": len(pos),
               "checks": checks}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    try:
        import tsebot.alerts as alerts
        icon = "✅" if not failed else "⚠️"
        body = "\n".join(("✔ " if c["ok"] else "✘ ") + c["check"] + " — " + c["detail"]
                         for c in checks)
        alerts.send(f"{icon} [WarRobot] آمادگی ۰۸:۴۰ — {payload['passed']}/{payload['total']}", body)
    except Exception:
        pass

    print(json.dumps({"passed": payload["passed"], "total": payload["total"],
                      "failed": [c["check"] for c in failed]}, ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
