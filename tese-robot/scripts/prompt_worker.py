"""Prompt worker: drains the Import Prompt inbox.

Runs as a background process (pm2:tse-prompt). Every few seconds it looks at
``state/prompt_inbox.jsonl``:

  * a pending command starting with ``!`` that matches a whitelisted action is
    executed immediately and marked ``done`` with its output;
  * any other pending command is marked ``awaiting_agent`` so the UI no longer
    looks stuck — the assistant picks those up on its next turn and writes the
    answer back through ``/prompt/result``.

Usage:  python scripts/prompt_worker.py [--interval 5]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "state" / "prompt_inbox.jsonl"
LOG = ROOT / "logs" / "prompt_worker.log"

SAFE = {
    "status": [sys.executable, "scripts/readiness_check.py"],
    "scan": [sys.executable, "scripts/run.py", "picks"],
    "report": [sys.executable, "scripts/daily_report.py"],
    "restart-web": ["pm2", "restart", "tse-web"],
    "restart-bot": ["pm2", "restart", "tse-bot"],
}


def now() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=3, minutes=30)).strftime("%Y-%m-%d %H:%M:%S")


def load() -> list:
    if not INBOX.exists():
        return []
    out = []
    for line in INBOX.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def save(rows: list) -> None:
    INBOX.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"[{now()}] {msg}\n")


def run_once() -> int:
    rows = load()
    changed = 0
    for r in rows:
        if r.get("status") != "pending":
            continue
        text = str(r.get("text") or "").strip()
        action = text.lstrip("!").strip().split()[0] if text.startswith("!") and text.strip() else ""
        if action and action in SAFE:
            try:
                proc = subprocess.run(SAFE[action], cwd=str(ROOT), capture_output=True,
                                      text=True, timeout=600)
                r["status"] = "done"
                r["result"] = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
            except Exception as exc:
                r["status"] = "error"
                r["result"] = str(exc)[:400]
            log(f"executed !{action} -> {r['status']}")
        else:
            r["status"] = "awaiting_agent"
            r["result"] = "در انتظار خوانده‌شدن توسط ایجنت"
            log(f"queued for agent: {text[:60]}")
        changed += 1
    if changed:
        save(rows)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    log("worker started")
    while True:
        try:
            run_once()
        except Exception as exc:
            log(f"error: {exc}")
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
