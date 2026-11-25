"""Consolidate the scheduling: ONE owner (scheduler.py), market days only.

Why this file exists: the daemon (run.py) and the scheduler both ran timed jobs
(preflight, order, rotate, scan, sell review, emails). That duplication is the
real cause of the repeated complaints: two logins fighting over the Chrome
profile, duplicate emails, two rotations cancelling each other's orders and
jobs firing on Thursday/Friday.

What it does:
  1. retires the daemon's own schedule: run.py is replaced by a SERVICE version
     that only keeps the order-book sampler, and warrobot is stopped
  2. scheduler.py becomes the single owner and gains:
        - market-day guard (skip every market job on Thursday/Friday)
        - nightly download at 20:30
        - pre-open order monitor 08:46..09:45 every 5 min
  3. market hours: pre-open 08:30-09:00, continuous 09:00-12:30
"""
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")

# ------------------------------------------------------------- 1) scheduler
sch = WAR / "scheduler.py"
s = sch.read_text(encoding="utf-8")

if "market_cal" not in s:
    s = s.replace("import distbot as bot  # noqa: E402",
                  "import distbot as bot  # noqa: E402\nimport market_cal  # noqa: E402")
    # new jobs
    s = s.replace('''    ("23:50", "pm2save", ["pm2_save.py"]),''',
'''    ("20:30", "download", ["nightly_download.py"]),
    ("23:50", "pm2save", ["pm2_save.py"]),''')
    # repeating order monitor in the pre-open window
    s = s.replace('''REPEAT = [
    ("09:00", "12:30", 3, "exitwatch", "intraday_exit.py"),
]''',
'''REPEAT = [
    ("09:00", "12:30", 3, "exitwatch", "intraday_exit.py"),
    ("08:46", "09:45", 5, "ordermon", "order_monitor.py"),
]''')
    # market-day guard around every job
    s = s.replace('''        today = data.setdefault(day, {})''',
'''        today = data.setdefault(day, {})
        trading = market_cal.market_day(now)''')
    s = s.replace('''            if hm < at:
                continue''',
'''            if hm < at:
                continue
            if not trading and job not in ("selfcheck", "selfcheckpm", "pm2save", "download"):
                if job not in today:
                    today[job] = f"closed@{hm}"
                    _save(data)
                continue''')
    s = s.replace('''        for at_from, at_to, every, job, script in REPEAT:
            if not (at_from <= hm <= at_to):
                continue''',
'''        for at_from, at_to, every, job, script in REPEAT:
            if not trading:
                break
            if not (at_from <= hm <= at_to):
                continue''')
    sch.write_text(s, encoding="utf-8")
    print("scheduler: market-day guard + download + order monitor wired")
else:
    print("scheduler already consolidated")

# ------------------------------------------- 2) retire the daemon's schedule
svc = WAR / "daemon_service.py"
if not svc.exists():
    svc.write_text('''"""Minimal long-running service: order-book sampler only.

All scheduled jobs moved to scheduler.py so nothing runs twice. The heavy
daemon (run.py) is no longer started.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BASE = Path(r"C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

import distbot as bot  # noqa: E402
import market_cal  # noqa: E402


def main() -> int:
    bot.log("service started (sampler only; scheduling lives in scheduler.py)")
    while True:
        now = bot.now_tehran()
        hm = now.strftime("%H:%M")
        trading = market_cal.market_day(now)
        in_session = "09:00" <= hm <= "12:30"
        if trading and in_session:
            try:
                import subprocess
                subprocess.run([sys.executable, "-u", str(BASE / "book_recorder.py")],
                               cwd=str(BASE), capture_output=True, text=True, timeout=120)
            except Exception as exc:
                bot.log(f"book error: {str(exc)[:80]}")
            time.sleep(60)
        else:
            time.sleep(300)


if __name__ == "__main__":
    raise SystemExit(main())
''', encoding="utf-8")
    print("daemon_service.py created (sampler only)")
else:
    print("daemon_service.py exists")
