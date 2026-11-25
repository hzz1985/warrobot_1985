"""Cross-process lock around the Playwright profile.

Root cause of the intermittent login/bind failures: several jobs (order, exit
watch, portfolio sync, readiness checks) can launch Chrome on the SAME profile at
the same time. Chrome's process-singleton then makes one of them attach to the
other instance (or fail), the session/captcha flow races, and the run reports
login_ok=false or an empty symbol search.

Every Playwright caller now takes this lock first, so the runs are serialized.
The lock is a plain file with an owner pid and a stale timeout, so a crashed run
can never block the bot forever.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
LOCK = BASE / "state" / "pw.lock"
STALE_SECONDS = 240          # a run longer than this is considered crashed
WAIT_SECONDS = 180           # how long a caller waits for the lock


def _owner() -> tuple[int, float]:
    try:
        parts = LOCK.read_text(encoding="utf-8").split("|")
        return int(parts[0]), float(parts[1])
    except Exception:
        return 0, 0.0


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True, timeout=15)
        return str(pid) in (out.stdout or "")
    except Exception:
        return True     # cannot tell -> assume alive


def _clean_profile() -> None:
    """Kill a Chrome left over by a crashed run before starting a new one."""
    try:
        import pw_reset
        n = pw_reset.kill_orphans()
        if n:
            print(f"[pw_lock] cleaned {n} orphan chrome process(es)", flush=True)
    except Exception:
        pass


@contextmanager
def guard(name: str = "pw", wait: int = WAIT_SECONDS):
    started = time.time()
    got = False
    while time.time() - started < wait:
        pid, ts = _owner()
        if pid == os.getpid():          # re-entrant: same process may nest runs
            got = True
            break
        stale = (time.time() - ts) > STALE_SECONDS
        if pid == 0 or stale or not _alive(pid):
            try:
                LOCK.parent.mkdir(parents=True, exist_ok=True)
                LOCK.write_text(f"{os.getpid()}|{time.time()}|{name}", encoding="utf-8")
                _clean_profile()
                got = True
                break
            except Exception:
                pass
        time.sleep(0.5)
    try:
        yield got
    finally:
        if got:
            try:
                pid, _ = _owner()
                if pid == os.getpid():
                    LOCK.unlink(missing_ok=True)
            except Exception:
                pass
            # release cleanup: a browser must never outlive its guarded run
            _clean_profile()


def is_locked() -> dict:
    pid, ts = _owner()
    return {"pid": pid, "age_sec": round(time.time() - ts, 1) if ts else None,
            "alive": _alive(pid) if pid else False, "stale": (time.time() - ts) > STALE_SECONDS if ts else True}
