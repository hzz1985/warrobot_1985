"""Pre-flight at 08:40 on the PATH THAT ACTUALLY ORDERS (Playwright).

1) log in through the OCR flow (a dead session is fixed, not just reported)
2) read the free balance (AccountBalance) and the open orders from the API
3) resolve the ISIN of every candidate
4) write state/preflight.json and exit non-zero when the bot is not ready
"""
from __future__ import annotations

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))

import pandas as pd  # noqa: E402
import market_cal  # noqa: E402

NODE = r"C:\Program Files\nodejs\node.exe"


def _clear_lock() -> None:
    prof = BASE / "state" / "pw-profile"
    for n in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (prof / n).unlink(missing_ok=True)
        except Exception:
            pass
PW = BASE / "pw"
OUT = BASE / "state" / "preflight.json"
CAND = BASE / "state" / "candidates.csv"


def _run_json(script: str, timeout: int) -> dict:
    import pw_lock
    with pw_lock.guard(f"preflight:{script}") as got:
        if not got:
            return {"ok": False, "reason": "playwright_lock_timeout"}
        return _run_json_locked(script, timeout)


def _run_json_locked(script: str, timeout: int) -> dict:
    try:
        p = subprocess.run([NODE, str(PW / script)], cwd=str(PW), capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return json.loads(line)
    except Exception as exc:
        return {"error": str(exc)[:80]}
    return {}


def _free_balance(broker: dict) -> float:
    flat = json.dumps(broker, ensure_ascii=False).replace("\\", "")
    m = re.search(r'"AccountBalance":([\d.]+)', flat)
    return float(m.group(1)) if m else 0.0


def _open_orders(broker: dict) -> list:
    flat = json.dumps(broker, ensure_ascii=False).replace("\\", "")
    return list(dict.fromkeys(re.findall(r'"symbol":"([^"]{2,20})"', flat)))


def _candidates() -> list:
    if not CAND.exists():
        return []
    import tsetmc as api
    df = pd.read_csv(CAND)
    out = []
    for _, r in df.iterrows():
        sym = str(r["symbol"])
        try:
            inst = api.resolve(sym)
            isin = str((inst or {}).get("isin") or "")
        except Exception:
            isin = ""
        out.append({"symbol": sym, "isin": isin, "ok": bool(isin)})
    return out


def main() -> int:
    if not market_cal.market_day():
        out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "skipped": market_cal.reason_closed(), "ready": None}
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False))
        return 0
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    out["login"] = _run_json("login.js", 600)
    broker = _run_json("check2.js", 600)
    out["free_balance"] = round(_free_balance(broker), 0)
    out["open_orders"] = _open_orders(broker)
    out["candidates"] = _candidates()
    out["buying_power"] = out["free_balance"]
    out["candidate_list_ok"] = bool(out["candidates"]) and all(c["ok"] for c in out["candidates"])
    out["ready"] = (bool(out["login"].get("ok")) and out["free_balance"] > 5_000_000
                    and out["candidate_list_ok"])
    if not out["candidate_list_ok"]:
        out["blocking_reason"] = "invalid or empty candidate list"
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False)[:700])
    return 0 if out["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
