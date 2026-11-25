"""Readiness receipt — proves (tonight) that tomorrow's ordering will work.

Runs the same chain the 08:45 job uses, in dry mode:
  1. calendar: is tomorrow a trading day (weekday + official holiday)?
  2. services: pm2 apps + web HTTP
  3. login/balance: real broker session
  4. price discovery: today's ceiling / 5-level book per candidate
  5. bind drill: FLOW_DRY=1 binds each planned symbol (no order is sent)
  6. sells: lot_exits --dry lists the lots due at TP/SL
  7. scheduler: the morning jobs are present in scheduler.py

Output: state/readiness.json  (verdict PASS/FAIL per line) and a printed summary.

    python readiness_check.py [--max 3]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

NODE = r"C:\Program Files\nodejs\node.exe"
PM2_BIN = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "pm2" / "bin" / "pm2"


def _pm2_raw(*args):
    """pm2 CLI through node — no cmd.exe window."""
    return subprocess.run([NODE, str(PM2_BIN), *args], capture_output=True, text=True,
                          timeout=120, encoding="utf-8", errors="replace")


BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "parent" / "Tse2" / "orderflow"))
STATE = BASE / "state"
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"
PY = sys.executable

OUT = STATE / "readiness.json"


def _pm2() -> dict:
    try:
        p = _pm2_raw("jlist")
        data = json.loads(p.stdout or "[]")
        return {x.get("name"): x.get("pm2_env", {}).get("status") for x in data}
    except Exception:
        return {}


def _http(url: str) -> int | str:
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status
    except Exception as exc:
        return f"ERR {str(exc)[:30]}"


def _json_tail(text: str) -> dict:
    for line in reversed((text or "").splitlines()):
        if line.strip().startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {}


def _candidates(n: int) -> list[dict]:
    import csv
    rows = []
    with (STATE / "candidates.csv").open(encoding="utf-8-sig") as fh:
        for row in list(csv.DictReader(fh))[:n]:
            rows.append(row)
    return rows


def _tomorrow() -> tuple[str, bool, str]:
    import market_cal
    tomorrow = market_cal.now_tehran() + timedelta(days=1)
    day = tomorrow.strftime("%Y-%m-%d")
    ok = market_cal.market_day(tomorrow)
    reason = market_cal.reason_closed(tomorrow) or "روز معاملاتی"
    return day, ok, reason


def main() -> int:
    max_n = 3
    if "--max" in sys.argv:
        max_n = int(sys.argv[sys.argv.index("--max") + 1])
    lines: list[dict] = []

    def add(name: str, ok: bool, detail="") -> None:
        lines.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})

    # 1) calendar
    try:
        day, ok_day, why = _tomorrow()
        add("تقویم فردا", ok_day, f"{day} · {why}")
    except Exception as exc:
        add("تقویم فردا", False, exc)

    # 2) services
    pm = _pm2()
    need = ("warrobot", "warsched", "tse-web", "tse-proxy", "tse-v6")
    if "--drill" in sys.argv:
        need = tuple(n for n in need if n != "warsched")  # paused on purpose during a drill
    add("سرویس‌ها", all(pm.get(n) == "online" for n in need),
        {n: pm.get(n) for n in need})
    add("وب‌اپ", _http("http://127.0.0.1:5077/") == 200, "HTTP 5077")

    # 3) login + balance (profile lock retries)
    import time as _time
    bal = None
    for attempt in range(3):
        try:
            if attempt:
                subprocess.run([PY, str(BASE / "pw_reset.py")], cwd=str(BASE),
                               capture_output=True, text=True, timeout=300)
                _time.sleep(4)
            p = subprocess.run([NODE, str(PW / "balance.js")], cwd=str(PW),
                               capture_output=True, text=True, timeout=600,
                               encoding="utf-8", errors="replace")
            bal = _json_tail((p.stdout or "") + (p.stderr or "")).get("account_balance")
            if bal:
                break
        except Exception:
            bal = None
    add("ورود کارگزار", bool(bal), f"موجودی {bal}")

    # 4) price discovery + 5) bind drill
    import price_discovery as pd
    cands = _candidates(max_n)
    for row in cands:
        sym = row["symbol"]
        try:
            d = pd.buy_price(sym, float(row.get("close") or 0))
            add(f"کشف قیمت {sym}", d["price"] > 0,
                f"{d['price']} · {d['source']} · باند {d['floor']}-{d['ceiling']}")
        except Exception as exc:
            add(f"کشف قیمت {sym}", False, exc)

    for row in cands:
        sym, isin = row["symbol"], row.get("isin") or ""
        bound, msg = "", ""
        for attempt in range(2):
            try:
                if attempt:
                    subprocess.run([PY, str(BASE / "pw_reset.py")], cwd=str(BASE),
                                   capture_output=True, text=True, timeout=300)
                    _time.sleep(4)
                env = dict(os.environ, FLOW_DRY="1")
                p = subprocess.run([NODE, str(PW / "flow.js"), sym, isin, "0", "0"],
                                   cwd=str(PW), capture_output=True, text=True, timeout=900,
                                   encoding="utf-8", errors="replace", env=env)
                d = _json_tail(p.stdout or "")
                bound = str(d.get("bound") or "")
                msg = str(d.get("message") or "")
                if bound:
                    break
            except Exception as exc:
                msg = str(exc)[:80]
        add(f"تمرین bind {sym}", bool(bound), bound[:120] or msg)

    # 6) sells due
    try:
        p = subprocess.run([PY, str(BASE / "lot_exits.py"), "--dry"], cwd=str(BASE),
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        d = _json_tail(p.stdout or "")
        due = d.get("due") or []
        add("فروش‌های سررسیدشده", True,
            "ندارد" if not due else json.dumps(due, ensure_ascii=False)[:200])
    except Exception as exc:
        add("فروش‌های سررسیدشده", False, exc)

    # 7) scheduler jobs
    try:
        sch = (BASE / "scheduler.py").read_text(encoding="utf-8")
        jobs = ["order", "lotsell", "preexit", "ordermon", "truth", "pwreset",
                "preopen_cleanup", "login"]
        missing = [j for j in jobs if j not in sch]
        add("زمان‌بند", not missing, "همهٔ کارهای صبح موجود" if not missing else missing)
    except Exception as exc:
        add("زمان‌بند", False, exc)

    verdict = all(x["ok"] for x in lines)
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "verdict": verdict,
           "checks": lines}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for x in lines:
        print(("PASS " if x["ok"] else "FAIL ") + x["check"] + " — " + str(x["detail"])[:140])
    print("\nVERDICT:", "PASS ✅" if verdict else "FAIL ❌")
    if not verdict:
        try:
            import market_cal
            bad = "\n".join(f"✘ {x['check']}: {x['detail']}" for x in lines if not x['ok'])
            market_cal.send_once(f"readiness:{out['at'][:10]}",
                                 "⚠️ [WarRobot] رسید آمادگی ناموفق",
                                 bad + "\n\nربات ممکن است سفارش‌گذاری نکند؛ بررسی لازم است.")
        except Exception:
            pass
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
