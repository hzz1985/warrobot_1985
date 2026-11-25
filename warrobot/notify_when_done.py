"""Notify by email when the BFomul calibration finishes.

Watches for the calibration process; once it is gone, reads the two workbooks and
emails a summary (symbols calibrated, how many pass the 75% precision bar, the
best ones, and how many went to WList).
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
sys.path.insert(0, str(BASE / "TeseRobot" / "src"))

OK = Path(r"C:\Users\Administrator\Desktop\bfomul_by_symbol.xlsx")
WL = Path(r"C:\Users\Administrator\Desktop\WList.xlsx")
LOG = BASE / "WarRobot" / "state" / "bfomul_cal.log"
PATTERN = "bfomul_calibrate"


def running() -> bool:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*{PATTERN}*' }} | Measure-Object | "
             "Select-Object -ExpandProperty Count"],
            capture_output=True, text=True, timeout=60)
        return int((out.stdout or "0").strip() or 0) > 0
    except Exception:
        return False


def main() -> int:
    # wait until the job disappears (max 8 hours)
    for _ in range(8 * 120):
        if not running():
            break
        time.sleep(30)

    import pandas as pd
    lines = ["کالیبراسیون BFomul تمام شد", f"زمان: {datetime.now():%Y-%m-%d %H:%M}", ""]
    n_ok = n_pass = n_wl = 0
    if OK.exists():
        df = pd.read_excel(OK)
        n_ok = len(df)
        n_pass = int(df["passes_75"].sum()) if "passes_75" in df.columns else 0
        lines.append(f"نمادهای کالیبره‌شده: {n_ok}")
        lines.append(f"قبولی با دقت ≥۷۵٪: {n_pass}")
        if n_ok:
            top = df.sort_values(["precision", "recall"], ascending=False).head(10)
            lines += ["", "بهترین ۱۰ نماد:"]
            for _, r in top.iterrows():
                lines.append(f"  {r['symbol']}: دقت {r['precision']}% · پوشش {r['recall']}% · "
                             f"صف بلند {r['long_episodes']} · سیگنال {r['signals']}")
            avg_p = round(float(df["precision"].mean()), 1)
            lines.append(f"\nمیانگین دقت همهٔ نمادها: {avg_p}%")
    else:
        lines.append("فایل bfomul_by_symbol.xlsx ساخته نشد.")
    if WL.exists():
        n_wl = len(pd.read_excel(WL))
        lines.append(f"نمادهای WList (بدون صف ۵ روزه): {n_wl}")
    if LOG.exists():
        tail = LOG.read_text(encoding="utf-8-sig", errors="replace").strip().splitlines()[-5:]
        lines += ["", "آخرین خطوط لاگ:"] + tail

    body = "\n".join(lines)
    print(body)
    try:
        from tsebot.alerts import send
        send("[WarRobot] کالیبراسیون BFomul تمام شد", body, key="bfomul-done", force=True)
        print("email sent")
    except Exception as exc:
        print("email error:", str(exc)[:120])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
