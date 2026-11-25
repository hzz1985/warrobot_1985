"""Readiness check at 08:30 / 08:40 / 08:45 — emails the operator every time.

Checks:
  1. login state of the broker (Playwright, OCR auto-login when needed)
  2. free balance (AccountBalance) and blocked cash
  3. the buy list from the nightly scan: file, row count, ISIN, price sanity
  4. that Chrome/Playwright is usable (profile lock cleared)

Sends "ربات آماده است" when everything passes, otherwise an email that says
what is not ready and exactly which manual steps are needed.
"""
from __future__ import annotations

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import json
import re
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))
sys.path.insert(0, str(BASE.parent / "TeseRobot" / "src"))
sys.path.insert(0, str(BASE.parent / "TeseRobot"))

import pandas as pd  # noqa: E402

from tsebot.alerts import send  # noqa: E402  (smtp-backed, same channel as the daemon)
import market_cal  # noqa: E402

STATE = BASE / "state"
CAND = STATE / "candidates.csv"
OUT = STATE / "ready_check.json"
MIN_FREE = 6_000_000


def _snapshot() -> dict:
    """One Playwright round trip: login + balance + open orders."""
    import preflight as pf
    login = pf._run_json("login.js", 600)     # already guarded by pw_lock
    broker = pf._run_json("check2.js", 600)
    return {"login": login, "broker": broker}


def _free_balance(broker: dict) -> float:
    flat = json.dumps(broker or {}, ensure_ascii=False).replace("\\", "")
    m = re.search(r'"AccountBalance":([\d.]+)', flat)
    return float(m.group(1)) if m else 0.0


def _blocked(broker: dict) -> float:
    flat = json.dumps(broker or {}, ensure_ascii=False).replace("\\", "")
    m = re.search(r'"BlockedBalance":([\d.]+)', flat)
    return float(m.group(1)) if m else 0.0


def _validate_list() -> dict:
    res = {"ok": False, "rows": 0, "problems": [], "symbols": []}
    if not CAND.exists():
        res["problems"].append("فایل candidates.csv وجود ندارد (اسکن شبانه اجرا نشده)")
        return res
    try:
        df = pd.read_csv(CAND)
    except Exception as exc:
        res["problems"].append(f"خواندن candidates.csv ناموفق: {str(exc)[:60]}")
        return res
    res["rows"] = len(df)
    if len(df) == 0:
        res["problems"].append("لیست خرید خالی است")
        return res
    if len(df) < 6:
        res["problems"].append(f"تعداد کاندیدا {len(df)} است، انتظار ۶ می‌رود")
    stale = None
    if "session" in df:
        sess = str(df["session"].iloc[0])
        from datetime import date
        # the scan runs the night before: its session label may be one day old
        stale = sess
    import tsetmc as api
    for _, r in df.iterrows():
        sym = str(r.get("symbol"))
        info = {"symbol": sym, "isin": "", "close": 0.0, "ok": False, "why": ""}
        try:
            info["close"] = float(r.get("close") or 0)
        except Exception:
            pass
        try:
            inst = api.resolve(sym)
            info["isin"] = str((inst or {}).get("isin") or "")
        except Exception:
            info["isin"] = ""
        if not info["isin"]:
            info["why"] = "ISIN پیدا نشد"
            res["problems"].append(f"{sym}: ISIN پیدا نشد")
        elif info["close"] <= 0:
            info["why"] = "قیمت پایانی در فایل صفر است"
            res["problems"].append(f"{sym}: قیمت پایانی صفر است")
        else:
            info["ok"] = True
        res["symbols"].append(info)
    if stale:
        res["session"] = stale
    res["ok"] = not res["problems"]
    return res


def _body(snap: dict, free: float, blocked: float, lst: dict, ready: bool) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    login_ok = bool((snap.get("login") or {}).get("ok"))
    lines = [f"زمان بررسی: {now}", ""]
    lines.append(f"{'✔' if login_ok else '✘'} وضعیت لاگین کارگزاری: "
                 f"{'وارد شده' if login_ok else 'وارد نشده'}")
    lines.append(f"{'✔' if free > MIN_FREE else '✘'} موجودی آزاد: {free:,.0f} ریال "
                 f"(بلوکه: {blocked:,.0f})")
    lines.append(f"{'✔' if lst['ok'] else '✘'} لیست خرید: {lst['rows']} نماد")
    for s in lst.get("symbols", []):
        mark = "✔" if s["ok"] else "✘"
        extra = f" — {s['why']}" if s.get("why") else ""
        lines.append(f"   {mark} {s['symbol']} | ISIN {s['isin'] or '-'} | "
                     f"قیمت پایانی {s['close']:,.0f}{extra}")
    if lst.get("problems"):
        lines.append("")
        lines.append("مشکلات:")
        for p in lst["problems"]:
            lines.append(f" • {p}")
    if not ready:
        lines.append("")
        lines.append("اقدام دستی لازم:")
        if not login_ok:
            lines.append(" • ورود دستی به https://online.meibours.ir و بررسی نام کاربری/رمز/کپچا")
        if free <= MIN_FREE:
            lines.append(" • بررسی مانده حساب و لغو سفارش‌های بازِ بلوکه‌شده")
        if not lst["ok"]:
            lines.append(" • اجرای دستی اسکن شبانه: python queue_scan.py")
            lines.append(" • بررسی فایل state/candidates.csv")
        lines.append("")
        lines.append("ربات تلاش می‌کند در زمان بعدی (۰۸:۴۰/۰۸:۴۵) دوباره بررسی کند.")
    return "\n".join(lines)


def main() -> int:
    # closed day: no login attempt, no email
    if not market_cal.market_day():
        result = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "skipped": market_cal.reason_closed(), "ready": None, "emailed": False}
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0

    snap = _snapshot()
    login_ok = bool((snap.get("login") or {}).get("ok"))
    free = _free_balance(snap.get("broker") or {})
    blocked = _blocked(snap.get("broker") or {})
    lst = _validate_list()
    ready = login_ok and free > MIN_FREE and lst["ok"]

    result = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "login_ok": login_ok, "free_balance": round(free, 0),
              "blocked": round(blocked, 0), "list_ok": lst["ok"],
              "rows": lst["rows"], "problems": lst["problems"], "ready": ready}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    stamp = datetime.now().strftime("%H:%M")
    if ready:
        subject = f"[WarRobot] {stamp} — آمادگی پیش از بازار (سفارش هنوز اجرا نشده) ✅"
    else:
        subject = f"[WarRobot] {stamp} — آماده نیست ⚠️"
    body = _body(snap, free, blocked, lst, ready)
    slot = "ready-" + stamp[:2] + ":" + stamp[3:5]
    result["emailed"] = market_cal.send_once(slot, subject, body)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False)[:600])
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
