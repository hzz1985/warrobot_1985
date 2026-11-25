"""Cross-process lock around the broker browser.

Root cause of the intermittent login/bind failures: several jobs (order, exit
watch, portfolio sync, readiness checks) can launch Chrome on the SAME profile at
the same time. Chrome's process-singleton then makes one of them attach to the
other instance (or fail), the session/captcha flow races, and the run reports
login_ok=false or an empty symbol search.

Hark fix #2 (what was still wrong here):
  * acquire was read-then-write (two processes could both "win");
    it is now an atomic O_CREAT|O_EXCL create;
  * a lock older than 240 s was "stale" and STOLEN from a live run (an order
    flow with OCR login easily takes > 240 s) and the thief then killed that
    run's Chrome -> session dropped mid-order. A lock is now stale only when its
    owner process is dead, or after STALE_SECONDS (15 min);
  * acquire/release no longer kill the shared, logged-in Chrome
    (pw_reset.kill_orphans only removes a Chrome that does not answer CDP).
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOCK = BASE / "state" / "pw.lock"
STALE_SECONDS = 900          # owner alive but holding longer than this -> crashed/hung
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
    if pid == os.getpid():
        return True
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except Exception:
            return True
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True, timeout=15)
        return str(pid) in (out.stdout or "")
    except Exception:
        return True     # cannot tell -> assume alive


def _clean_profile() -> None:
    """Remove a Chrome that holds the profile without answering (crashed run).

    The shared broker Chrome is left alone (see pw_reset.kill_orphans)."""
    try:
        import pw_reset
        n = pw_reset.kill_orphans()
        if n:
            print(f"[pw_lock] cleaned {n} orphan chrome process(es)", flush=True)
    except Exception:
        pass


def _try_create(name: str) -> bool:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()}|{time.time()}|{name}")
    return True


@contextmanager
def guard(name: str = "pw", wait: int = WAIT_SECONDS):
    started = time.time()
    got = False
    nested = False
    while time.time() - started < wait:
        pid, ts = _owner()
        if pid == os.getpid():          # re-entrant: same process may nest runs
            got, nested = True, True
            break
        if _try_create(name):
            got = True
            _clean_profile()
            break
        pid, ts = _owner()
        stale = ts and (time.time() - ts) > STALE_SECONDS
        if pid == 0 and not ts:
            # file vanished or unreadable mid-write: retry soon
            time.sleep(0.2)
            continue
        if stale or not _alive(pid):
            try:
                LOCK.unlink(missing_ok=True)
            except Exception:
                pass
            continue
        time.sleep(0.5)
    try:
        yield got
    finally:
        if got and not nested:
            try:
                pid, _ = _owner()
                if pid == os.getpid():
                    LOCK.unlink(missing_ok=True)
            except Exception:
                pass


def is_locked() -> dict:
    pid, ts = _owner()
    return {"pid": pid, "age_sec": round(time.time() - ts, 1) if ts else None,
            "alive": _alive(pid) if pid else False,
            "stale": (time.time() - ts) > STALE_SECONDS if ts else True}
