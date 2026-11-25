"""Minimal long-running service: order-book sampler only.

All scheduled jobs moved to scheduler.py so nothing runs twice. The heavy
daemon (run.py) is no longer started.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
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
