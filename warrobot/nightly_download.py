"""Nightly market-data refresh (moved out of the old daemon).

Runs the archived updater against the legacy project, exactly like the daemon
used to at 20:30. The scheduler owns it now, so the daemon can be retired and
nothing is scheduled twice.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

import distbot as bot  # noqa: E402


def main() -> int:
    script = bot.LEGACY / "scripts" / "update_data.py"
    if not script.exists():
        print(f"download script missing: {script}")
        return 1
    bot.log("nightly download started (scheduler)")
    proc = subprocess.run([sys.executable, "-u", str(script)], cwd=str(bot.LEGACY),
                          capture_output=True, text=True, timeout=3600,
                          encoding="utf-8", errors="replace")
    tail = ((proc.stdout or "") + (proc.stderr or ""))[-800:]
    bot.log(f"nightly download rc={proc.returncode} :: {tail[-300:]}")
    (BASE / "state" / "last_download.txt").write_text(
        f"{datetime.now().strftime('%Y-%m-%d')}|rc={proc.returncode}", encoding="utf-8")
    print(f"download rc={proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
