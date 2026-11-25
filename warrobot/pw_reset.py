"""Kill orphan Chrome processes that still hold the bot's Playwright profile.

Root cause of the repeated "free balance = 0 / no order" days:
  a crashed Playwright run leaves Chrome alive with
  --user-data-dir=state/pw-profile. The next run then fails with
  "Opening in existing browser session" — silently, and every Python caller
  degrades (balance 0, no open orders, no order).

This module is called:
  * by pw_lock.guard() right after it acquires the lock (so every guarded run
    starts from a clean profile)
  * by the scheduler at 08:44 (job "pwreset") right before the pre-open order
  * manually: python pw_reset.py --verbose
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent   # == ...\DeepSeek DeskTop\WarRobot on the server
PROFILE = BASE / "state" / "pw-profile"
LOCKS = ("SingletonLock", "SingletonCookie", "SingletonSocket")


def _shared_browser_alive() -> bool:
    try:
        import broker_browser
        return broker_browser.shared_enabled() and broker_browser.cdp_alive()
    except Exception:
        return False


def kill_orphans(verbose: bool = False, force: bool = False) -> int:
    """Kill chrome.exe whose command line points at our profile.

    Chrome children appear/disappear asynchronously, so sweep a few times until
    the profile is really free (a single pass left 8 processes behind).

    Hark fix #2: this used to run before AND after every guarded run, after every
    scheduler job and at 08:44 - i.e. it killed the logged-in broker session right
    before the 08:45 order and dozens of times per hour. Now the shared Chrome
    (debugging port answering) is never killed unless force=True; only a Chrome
    that holds the profile WITHOUT answering (a real orphan/hang) is removed.
    """
    if not force and _shared_browser_alive():
        if verbose:
            print("shared broker Chrome is alive - nothing killed")
        return 0
    killed = 0
    for _round in range(6):
        n = _kill_once(verbose)
        killed += n
        if n == 0:
            break
        import time
        time.sleep(0.7)
    clean_locks(verbose)
    return killed


def _kill_once(verbose: bool = False) -> int:
    killed = 0
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
              "Where-Object { $_.CommandLine -like '*pw-profile*' } | "
              "ForEach-Object { $_.ProcessId }")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60)
        pids = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip().isdigit()]
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           capture_output=True, text=True, timeout=20)
            killed += 1
            if verbose:
                print("killed chrome pid", pid)
    except Exception as exc:
        if verbose:
            print("kill_orphans error:", str(exc)[:120])
    return killed


def clean_locks(verbose: bool = False) -> None:
    for name in LOCKS:
        p = PROFILE / name
        try:
            if p.exists() or p.is_symlink():
                p.unlink(missing_ok=True)
                if verbose:
                    print("removed", name)
        except Exception:
            pass
    # stale pw.lock owned by a dead process
    lock = BASE / "state" / "pw.lock"
    try:
        if lock.exists():
            parts = lock.read_text(encoding="utf-8").split("|")
            pid = int(parts[0])
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                                 capture_output=True, text=True, timeout=15)
            if str(pid) not in (out.stdout or ""):
                lock.unlink(missing_ok=True)
                if verbose:
                    print("removed stale pw.lock (pid", pid, ")")
    except Exception:
        pass


def reset(verbose: bool = False, force: bool = False) -> dict:
    killed = kill_orphans(verbose, force=force)
    return {"killed_chrome": killed, "profile": str(PROFILE)}


if __name__ == "__main__":
    print(reset(verbose="--verbose" in sys.argv, force="--force" in sys.argv))
