# -*- coding: utf-8 -*-
"""Run the very same dashboard on port 5000 (legacy bookmark).

The old webapp lived on :5000, so a bookmark pointing there would show
"unable to connect" after the rewrite. This launcher just sets the port and
executes the same app, so both URLs render identical pages.
"""
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["TSEBOT_WEB_PORT"] = "5000"
sys.path.insert(0, str(ROOT / "webapp"))
runpy.run_path(str(ROOT / "webapp" / "app.py"), run_name="__main__")
