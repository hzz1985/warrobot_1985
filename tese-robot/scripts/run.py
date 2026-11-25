# -*- coding: utf-8 -*-
"""Launcher: python scripts\\run.py <command> [options]"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tsebot.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
