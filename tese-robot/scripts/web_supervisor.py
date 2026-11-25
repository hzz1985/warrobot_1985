# -*- coding: utf-8 -*-
"""Keep BOTH dashboard ports alive.

The dashboard is served on:
    5077  main
    5000  legacy bookmark (the old webapp used it)

Each child is restarted automatically if it dies, so the page can never stay
down because of one bad request or a crashed process.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
TARGETS = [
    (5077, ROOT / "webapp" / "app.py"),
    (5000, ROOT / "webapp" / "app.py"),
]

procs = {}
print(f"[supervisor] python={PY}", flush=True)
while True:
    for port, script in TARGETS:
        p = procs.get(port)
        if p is None or p.poll() is not None:
            if p is not None:
                print(f"[supervisor] :{port} exited rc={p.returncode}; restarting", flush=True)
            env = dict(os.environ)
            env["TSEBOT_WEB_PORT"] = str(port)
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                procs[port] = subprocess.Popen([PY, "-u", str(script)], cwd=str(ROOT), env=env)
                print(f"[supervisor] :{port} started pid={procs[port].pid}", flush=True)
            except Exception as exc:
                print(f"[supervisor] :{port} spawn failed: {exc}", flush=True)
    time.sleep(3)



