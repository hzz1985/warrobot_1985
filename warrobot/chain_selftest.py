"""Self-test of the readiness chain: run every check and send a proof email.

Prints a step-by-step report and emails it, so the operator gets the assurance
message even outside the 08:30 / 08:40 / 08:45 schedule.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent / "TeseRobot" / "src"))
sys.path.insert(0, str(BASE.parent / "TeseRobot"))

from tsebot.alerts import send  # noqa: E402

PY = sys.executable


def _step(title: str, script: str, args: list[str] | None = None) -> tuple[bool, str]:
    try:
        p = subprocess.run([PY, "-u", str(BASE / script)] + list(args or []), cwd=str(BASE),
                           capture_output=True, text=True, timeout=1800,
                           encoding="utf-8", errors="replace")
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        ok = p.returncode == 0
        return ok, out[-400:]
    except Exception as exc:
        return False, str(exc)[:200]


def main() -> int:
    lines = [f"آزمون زنجیرهٔ آمادگی — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
    all_ok = True

    ok, out = _step("بررسی آمادگی (لاگین + لیست + موجودی)", "ready_check.py")
    lines.append(f"{'✔' if ok else '⚠'} بررسی آمادگی: {'آماده' if ok else 'ناآماده'}")
    try:
        d = json.loads((BASE / "state" / "ready_check.json").read_text(encoding="utf-8"))
        lines.append(f"   لاگین: {'✔ وارد شده' if d.get('login_ok') else '✘ وارد نشده'}")
        lines.append(f"   موجودی آزاد: {d.get('free_balance', 0):,.0f} ریال")
        lines.append(f"   لیست خرید: {d.get('rows', 0)} نماد — "
                     f"{'سالم' if d.get('list_ok') else 'مشکل دارد'}")
        lines.append(f"   ایمیل بررسی: {'ارسال شد' if d.get('emailed') else 'ارسال نشد'}")
        if d.get("problems"):
            for p in d["problems"]:
                lines.append(f"     • {p}")
    except Exception as exc:
        lines.append(f"   خواندن نتیجه ناموفق: {str(exc)[:80]}")
    lines.append("")

    ok2, out2 = _step("سایزینگ سفارش (بدون ارسال)", "order_candidates.py", ["--dry"])
    first = out2.replace("\n", " ")[:180]
    lines.append(f"{'✔' if ok2 else '⚠'} آزمون سایزینگ: {first}")
    lines.append("")

    ok3, out3 = _step("آزمون چرخش ۱۰:۰۰ (بدون اجرا)", "rebalance.py", ["--dry"])
    first3 = out3.replace("\n", " ")[:180]
    lines.append(f"{'✔' if ok3 else '⚠'} آزمون چرخش: {first3}")
    lines.append("")

    # web app
    try:
        import urllib.request
        html = urllib.request.urlopen("http://127.0.0.1:5077/", timeout=60).read().decode("utf-8", "replace")
        web_ok = "<html" in html.lower() and "PRO-THEME" in html
        lines.append(f"{'✔' if web_ok else '✘'} وب‌اپ: {len(html):,} بایت")
    except Exception as exc:
        web_ok = False
        lines.append(f"✘ وب‌اپ: {str(exc)[:80]}")
    lines.append("")
    lines.append("نتیجه: " + ("همهٔ اجزا سالم هستند ✅" if (ok and web_ok) else "مواردی نیاز به بررسی دارند ⚠️"))

    body = "\n".join(lines)
    try:
        send("[WarRobot] آزمون زنجیرهٔ آمادگی — گزارش کامل", body, key="chain-selftest", force=True)
        lines.append("\n(این گزارش به ایمیل ارسال شد)")
        sent = True
    except Exception as exc:
        lines.append(f"\n(ارسال ایمیل ناموفق: {str(exc)[:80]})")
        sent = False

    (BASE / "state" / "chain_selftest.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0 if (ok and web_ok and sent) else 2


if __name__ == "__main__":
    raise SystemExit(main())
