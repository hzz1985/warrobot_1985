"""Reuse the operator's existing Chrome login instead of typing the password again.

Situation: the operator is logged into online.meibours.ir in his normal Chrome,
while the bot runs a separate Playwright profile and therefore asks for the login
again (captcha + password) on a second window.

Chrome cannot be driven while it is already running on its profile, and Chrome
136+ refuses CDP on the default profile — but the *session cookies* can be copied
into the bot profile on the same Windows account (same DPAPI key, same Chrome
build), which leaves the bot already authenticated when it starts.

What this script does:
  1. finds every Chrome profile's cookie database
  2. picks the most recently written one (that is the one being used now)
  3. backs up the bot profile cookie DB and copies in Local State + Cookies(+wal)
  4. verifies the session with a real broker call
Run it before the pre-open chain (scheduler 08:38).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"
BOT_PROFILE = BASE / "state" / "pw-profile"
OUT = BASE / "state" / "login_sync.json"
CHROME_USER_DATA = Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"


def cookie_dbs() -> list[Path]:
    out = []
    if not CHROME_USER_DATA.exists():
        return out
    for d in CHROME_USER_DATA.glob("*"):
        if not d.is_dir():
            continue
        for sub in ("Network", "."):
            p = d / sub / "Cookies"
            if p.exists():
                out.append(p)
    return out


def newest_cookies() -> Path | None:
    dbs = cookie_dbs()
    if not dbs:
        return None
    return max(dbs, key=lambda p: p.stat().st_mtime)


def copy_session(src_cookies: Path) -> dict:
    src_profile = src_cookies.parent.parent if src_cookies.parent.name == "Network" else src_cookies.parent
    src_local_state = src_profile / "Local State"
    dst_net = BOT_PROFILE / "Network"
    dst_net.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    copied = {}

    # backup the bot's current cookie DB
    for name in ("Cookies", "Cookies-wal", "Cookies-shm"):
        p = dst_net / name
        if p.exists():
            shutil.copy2(p, p.with_name(f"{name}.bak{stamp}"))
            copied.setdefault("backups", []).append(str(p))

    for src, dst in ((src_cookies, dst_net / "Cookies"),
                     (src_cookies.with_name("Cookies-wal"), dst_net / "Cookies-wal"),
                     (src_cookies.with_name("Cookies-shm"), dst_net / "Cookies-shm"),
                     (src_local_state, BOT_PROFILE / "Local State")):
        try:
            if src.exists():
                shutil.copy2(src, dst)
                copied.setdefault("files", []).append(dst.name)
        except Exception as exc:
            copied.setdefault("errors", []).append(f"{dst.name}: {str(exc)[:60]}")
    return copied


def verify_session() -> dict:
    try:
        p = subprocess.run([NODE, str(PW / "broker_client.js"), "account"],
                           cwd=str(PW), capture_output=True, text=True,
                           timeout=600, encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                acc = (json.loads(line).get("account") or {})
                return {"ok": bool(acc.get("AccountBalance")),
                        "balance": acc.get("AccountBalance")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}
    return {"ok": False, "error": "no response"}


def main() -> int:
    src = newest_cookies()
    result = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "source": str(src) if src else None}
    if src is None:
        result["status"] = "no Chrome cookie database found"
    else:
        result["copied"] = copy_session(src)
        result["verify"] = verify_session()
        result["status"] = "session reused" if result["verify"].get("ok") else "session not valid"
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"status": result["status"], "source": result["source"],
                      "verify": result.get("verify")}, ensure_ascii=False))
    return 0 if result.get("status") == "session reused" else 1


if __name__ == "__main__":
    raise SystemExit(main())
