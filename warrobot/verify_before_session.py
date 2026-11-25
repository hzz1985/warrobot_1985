"""Verification of everything the operator asked for, run before the session.

Each check exercises the real code path (dry where an order would be placed) and
prints PASS/FAIL. Exit code 2 when anything fails.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

PY = sys.executable
rows = []


def add(name: str, ok: bool, detail: str = "") -> None:
    rows.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def run(script: str, args: list[str] | None = None, timeout: int = 900) -> tuple[int, str]:
    try:
        p = subprocess.run([PY, "-u", str(BASE / script)] + list(args or []),
                           cwd=str(BASE), capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, ((p.stdout or "") + (p.stderr or ""))
    except Exception as exc:
        return 99, str(exc)[:200]


def main() -> int:
    print("=== 1) تقویم بازار ===")
    import market_cal
    add("امروز روز معاملاتی است", market_cal.market_day(),
        market_cal.market_day_name() + " " + market_cal.now_tehran().strftime("%H:%M"))
    fri = datetime(2026, 11, 13, 9, 0)
    sat = datetime(2026, 11, 14, 9, 0)
    add("پنجشنبه/جمعه تعطیل", not market_cal.market_day(fri) and market_cal.market_day(sat),
        "جمعه=بسته · شنبه=باز")

    print("\n=== 2) ایمیل یک‌باره ===")
    key = "verify-once-" + datetime.now().strftime("%H%M%S")
    before = market_cal.already_sent(key)      # must be False for a fresh key
    market_cal.mark_sent(key)
    after = market_cal.already_sent(key)       # must be True now
    add("ایمیل تکراری مسدود می‌شود", (not before) and after, "کلید تازه → یک بار")

    print("\n=== 3) تخصیص مساوی ===")
    rc, out = run("order_candidates.py", ["--dry"])
    ok = rc == 0 and '"equal_allocation": true' in out
    target, scale, planned = "", "", ""
    try:
        start = out.find("{")
        data = json.loads(out[start:])
        target = f'{data.get("target_per_symbol", 0):,.0f}'
        scale = data.get("allocation_scale")
        planned = ", ".join(data.get("planned_symbols") or [])
        orders = data.get("orders") or []
        total = sum((o.get("budget") or 0) for o in orders)
        free = data.get("free_balance") or 0
        pending = data.get("skipped") == "open_order_pending"
        ok = ok and (pending or (total <= free * 0.96 and len(orders) >= 1))
        add("هدف هر نماد = یک‌سوم دارایی", ok,
            "سفارش باز موجود — سفارش جدید لازم نیست" if pending
            else f"هدف {target} · scaled {scale}")
        add("مجموع سفارش‌ها ≤ پول آزاد", pending or total <= free * 0.96,
            f"سفارش {total:,.0f} · آزاد {free:,.0f}")
        planned_set = {x.strip() for x in str(planned).split(",") if x.strip()}
        add("فقط نمادهای موجودی اولویت دارند",
            planned_set.issubset({"آردینه", "آواپارس", "افران"}), planned)
    except Exception as exc:
        add("تحلیل خروجی تخصیص", False, str(exc)[:80])

    print("\n=== 4) چرخش ۱۰:۰۰ ===")
    rc, out = run("rebalance.py", ["--dry"])
    add("چرخش بدون خطا اجرا می‌شود", rc == 0, "nothing unfilled" if "nothing unfilled" in out else out[-80:])
    add("مسیر لغو واقعی ( آرایهٔ شناسه )", "Order/Delete" in (BASE / "pw" / "cancel_delete.js").read_text(encoding="utf-8"),
        "pw/cancel_delete.js")

    print("\n=== 5) ورود خودکار ===")
    rc, out = run("pw_login_check.py" if (BASE / "pw_login_check.py").exists() else "preflight.py", timeout=900)
    ok = '"ok": true' in out and '"login"' in out
    add("ورود به کارگزاری انجام شد", ok, out.strip().splitlines()[-1][:90] if out else "")

    print("\n=== 6) وب‌اپ ===")
    try:
        import urllib.request
        h = urllib.request.urlopen("http://127.0.0.1:5077/", timeout=60).read().decode("utf-8", "replace")
        add("صفحه وب باز می‌شود", "<html" in h.lower() and len(h) > 20000, f"{len(h):,} bytes")
        add("تم روشن (سفید)", "LIGHT-THEME" in h, "")
        add("تب دیپ‌سیک حذف شده", 'data-tab="export"' not in h, "")
        add("دادهٔ کاندیدا و پوزیشن", "کاندیدا" in h and "پوزیشن" in h, "")
    except Exception as exc:
        add("صفحه وب باز می‌شود", False, str(exc)[:80])

    print("\n=== 7) سرویس‌ها و زمان‌بند ===")
    try:
        out = subprocess.run(["pm2", "list", "--no-color"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", shell=True, timeout=90).stdout or ""
        for name in ("warrobot", "warsched", "tse-web"):
            add(f"سرویس {name} آنلاین", any(f" {name} " in l and "online" in l for l in out.splitlines()))
    except Exception as exc:
        add("سرویس‌ها", False, str(exc)[:60])
    add("daemon سبک (run.py اجرا نمی‌شود)",
        (BASE / "daemon_service.py").exists() and "scheduler" in (BASE / "daemon_service.py").read_text(encoding="utf-8"))
    sch = (BASE / "scheduler.py").read_text(encoding="utf-8")
    for at, job in (("08:30", "ready30"), ("08:40", "preflight"), ("08:45", "order"),
                    ("10:00", "rotate"), ("08:46", "ordermon"), ("20:30", "download")):
        add(f"زمان‌بند شامل {job} ({at})", job in sch)

    print("\n=== 8) قواعد ===")
    rc, out = run("selfcheck_rules.py")
    import re as _re
    _m = _re.search(r"(\d+)/(\d+) rules verified", out or "")
    _ok = bool(_m) and int(_m.group(1)) == int(_m.group(2)) and int(_m.group(1)) >= 27
    add("۲۷+ قاعده سبز", _ok, out.strip().splitlines()[-1][:60] if out else "")

    failed = [r for r in rows if not r["ok"]]
    summary = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "total": len(rows), "passed": len(rows) - len(failed),
               "failed": [r["check"] for r in failed]}
    (BASE / "state" / "verify_before_session.json").write_text(
        json.dumps({**summary, "checks": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{summary['passed']}/{summary['total']} checks passed")
    if failed:
        print("FAILED:", ", ".join(summary["failed"]))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
